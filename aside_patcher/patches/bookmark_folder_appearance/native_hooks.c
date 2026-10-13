/* Exact hook entry points are guarded and relocated by the payload builder. */
#include "native_ui.h"
#include "native_background.h"
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
void BfaRowBorderHook(void *view,void *canvas) {
 unsigned char *row=view;
 int inset=*(int *)(row+0x464)==1?BfaNestedRowInset(view):0;
 if(!inset){BfaOriginalRowBorder(view,canvas);return;}
 *(int *)(row+0x464)=0;BfaOriginalRowBorder(view,canvas);*(int *)(row+0x464)=1;
 BfaPaintInsetOutline(view,canvas,inset);
}
