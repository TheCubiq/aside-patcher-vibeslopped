/* Exact hook entry points are guarded and relocated by the payload builder. */
#include "native_ui.h"
#include "native_background.h"
#include <intrin.h>
extern void *BfaOriginalOwnerConstructor(void *,void *,void *,void *);
extern void *BfaOriginalOwnerDestructor(void *,unsigned);
extern void BfaOriginalRowIcon(void *);
extern void *BfaOriginalRowDestructor(void *,unsigned);
extern void BfaOriginalFolderMenu(void *,void *,void *);
extern void BfaOriginalModelRemove(void *,const void *,unsigned,const void *);
extern void BfaOriginalViewBackground(void *,void *);
extern void BfaOriginalRowBackground(void *,void *);
extern void BfaOriginalRowBorder(void *,void *);
extern void BfaPaintInsetOutline(void *,void *,int);
extern void BfaOriginalRowMask(void *);
extern int BfaColoredFolder(void *);
extern void BfaRefreshRowMask(void *,int);
extern void BfaOriginalRowsPaint(void *,void *);
extern void BfaSuppressContainerOutline(void *,int);
extern void BfaPaintInsetSelectedOutline(void *,void *,int);
extern void BfaRowStatePainted(void *);
extern int BfaPaintDraggingFolder(void *,void *);
extern void BfaOriginalLayoutImmediately(void *);
extern void BfaCaptureFolderTargets(void *);
extern void BfaBeginFolderTransition(void *,void *);
extern void BfaOriginalFolderToggle(void *,void *,void *);
extern void BfaOriginalAnimationProgress(void *);
extern void BfaFolderAnimationProgress(void *);
extern void BfaOriginalRowAnimationProgress(void *,void *);
extern void BfaRowAnimationProgress(void *,void *);
extern void *BfaOriginalPreferredSize(void *,void *,void *);
extern void BfaPrepareFolderMeasurement(void *,int);
extern void BfaFinishFolderUpdate(void *);
void *BfaPreferredSizeHook(void *view,void *size,void *bounds) {
 /* Prepare every new measurement except the owner's earlier old-height snapshot. */
 if(_ReturnAddress()!=BfaNativeAddress(0x388672c))
  BfaPrepareFolderMeasurement(view,_ReturnAddress()==BfaNativeAddress(0x3886e63));
 return BfaOriginalPreferredSize(view,size,bounds);
}
void BfaRowAnimationProgressHook(void *animator,void *animation) {
 BfaOriginalRowAnimationProgress(animator,animation);
 BfaRowAnimationProgress(animator,animation);
}
void BfaFolderToggleHook(void *owner,void *node,void *event) {
 BfaBeginFolderTransition(owner,node);
 BfaOriginalFolderToggle(owner,node,event);
 BfaFinishFolderUpdate(owner);
}
void BfaAnimationProgressHook(void *owner) {
 BfaOriginalAnimationProgress(owner);
 BfaFolderAnimationProgress(owner);
}
void BfaLayoutImmediatelyHook(void *view) {
 BfaPrepareFolderPaint(view);
 BfaOriginalLayoutImmediately(view);
 BfaCaptureFolderTargets(view);
}
void *BfaOwnerConstructorHook(void *owner,void *browser,void *view,void *model) {
 void *result=BfaOriginalOwnerConstructor(owner,browser,view,model);
 BfaOwner *context=BfaRetainOwner(owner);BfaReleaseOwner(context);return result;
}
void *BfaOwnerDestructorHook(void *owner,unsigned flags) {
 BfaOwnerDestroyed(owner);return BfaOriginalOwnerDestructor(owner,flags);
}
void BfaRowIconHook(void *row) {BfaOriginalRowIcon(row);BfaRowUpdated(row);}
void *BfaRowDestructorHook(void *row,unsigned flags) {
 BfaRowDestroyed(row);return BfaOriginalRowDestructor(row,flags);
}
void BfaFolderMenuHook(void *owner,void *row,void *point) {
 if(!BfaShowFolderMenu(owner,row,point))BfaOriginalFolderMenu(owner,row,point);
}
void BfaModelRemoveHook(void *model,const void *node,unsigned source,const void *location) {
 BfaModelRemoving(model,node);BfaOriginalModelRemove(model,node,source,location);
}
void BfaViewBackgroundHook(void *view,void *canvas) {
 BfaPrepareFolderPaint(view);BfaOriginalViewBackground(view,canvas);
}
void BfaRowBackgroundHook(void *view,void *canvas) {
 unsigned char *row=view;
 BfaRowStatePainted(view);
 if(row[0x458] && *(int *)(row+0x464)==0 && BfaColoredFolder(view)){
  unsigned char hovered=row[0x453],selected=row[0x450],dragged=row[0x458];
  row[0x453]=0;row[0x450]=0;row[0x458]=0;BfaOriginalRowBackground(view,canvas);
  row[0x453]=hovered;row[0x450]=selected;row[0x458]=dragged;
  if(BfaPaintDraggingFolder(view,canvas))return;
  BfaOriginalRowBackground(view,canvas);return;
 }
 if(BfaColoredFolder(view) && !row[0x458] && *(int *)(row+0x464)==0){
  unsigned char hovered=row[0x453],selected=row[0x450];
  row[0x453]=0;row[0x450]=0;BfaOriginalRowBackground(view,canvas);
  row[0x453]=hovered;row[0x450]=selected;return;
 }
 /* Insertion indicators retain their native path; folder targets use inset fill. */
 int drop=*(int *)(row+0x464);
 if(drop>1 || (!row[0x450] && !row[0x453] && !row[0x458] && drop!=1)) {
  BfaOriginalRowBackground(view,canvas);return;
 }
 int inset=BfaNestedRowInset(view);
 if(!inset) {
  BfaOriginalRowBackground(view,canvas);return;
 }
 unsigned char selected=row[0x450],hovered=row[0x453],dragged=row[0x458];
 row[0x450]=0;row[0x453]=0;row[0x458]=0;*(int *)(row+0x464)=0;
 BfaOriginalRowBackground(view,canvas);
 row[0x450]=selected;row[0x453]=hovered;row[0x458]=dragged;*(int *)(row+0x464)=drop;
 /* Native paint clears the row's layer first; draw after that clear. */
 if(!BfaPaintInsetRow(view,canvas,inset))BfaOriginalRowBackground(view,canvas);
}
void BfaRowMaskHook(void *view) {
 BfaOriginalRowMask(view);
 BfaRefreshRowMask(view,1);
}
void BfaRowsPaintHook(void *view,void *canvas) {
 BfaSuppressContainerOutline(view,1);
 BfaOriginalRowsPaint(view,canvas);
 BfaSuppressContainerOutline(view,0);
}
void BfaRowBorderHook(void *view,void *canvas) {
 unsigned char *row=view;
 int inset=*(int *)(row+0x464)==1?BfaNestedRowInset(view):0;
 if(!inset){
  BfaOriginalRowBorder(view,canvas);
  if(row[0x450] || row[0x458]==1){
   inset=BfaNestedRowInset(view);
   if(inset && !BfaColoredFolder(view))BfaPaintInsetSelectedOutline(view,canvas,inset);
  }
  return;
 }
 *(int *)(row+0x464)=0;BfaOriginalRowBorder(view,canvas);*(int *)(row+0x464)=1;
 BfaPaintInsetOutline(view,canvas,inset);
}
