#include "native_background.h"
#define NATIVE_CALLS __declspec(guard(nocf))

/* Native Background virtual slots are verified against the vendor image. */
typedef struct {
 void **methods;
 uintptr_t transparent;
 BfaOwner *owner;
 void *original;
 void *rounded;
} FolderBackground;
NATIVE_CALLS static void destroy_background(void *object) {
 if(!object)return;
 void (*destroy)(void*,unsigned)=*(void (**)(void*,unsigned))*(uintptr_t *)object;
 destroy(object,1);
}
NATIVE_CALLS static void destroy_folder_background(FolderBackground *self,unsigned flags) {
 destroy_background(self->original);destroy_background(self->rounded);
 BfaReleaseOwner(self->owner);
 BfaFree(self->methods,56);
 if(flags&1)BfaFree(self,sizeof(*self));
}
NATIVE_CALLS static void paint_folder_background(FolderBackground *self,void *canvas,void *view) {
 if(self->original){
  void (*paint)(void*,void*,void*)=*(void (**)(void*,void*,void*))(*(uintptr_t *)self->original+0x20);
  paint(self->original,canvas,view);
 }
 if(BfaOwnerPointer(self->owner))BfaPaintFolderBlocks(self->owner,canvas,view,self->rounded);
}
int BfaHasFolderBackground(void *view) {
 void *background=*(void **)((unsigned char *)view+0x228);
 return background && ((void **)*(uintptr_t *)background)[4]==(void *)paint_folder_background;
}
NATIVE_CALLS int BfaPaintInsetRow(void *raw_row,void *canvas,int inset) {
 unsigned char *row=raw_row;
 void *parent=*(void **)(row+0x198);
 if(!parent || !BfaHasFolderBackground(parent))return 0;
 FolderBackground *background=*(FolderBackground **)((unsigned char *)parent+0x228);
 void *widget=*(void **)(row+0x190);if(!widget)return 0;
 void *provider=((void *(*)(void*))BfaNativeAddress(0x5433810))((unsigned char *)widget+0x48);
 if(!provider)return 0;
 unsigned char *rounded=background->rounded;
 *(int *)(rounded+0x20)=0;*(int *)(rounded+0x24)=inset;
 *(int *)(rounded+0x28)=0;*(int *)(rounded+0x2c)=6;
 /* Reproduce the native base fill and folder-drop fill in the same order. */
 int colors[2],count=0;
 if(row[0x450] || row[0x458]==1)colors[count++]=0xd6;
 else if(row[0x453]==1)colors[count++]=0xf7;
 if(*(int *)(row+0x464)==1)colors[count++]=0xdb;
 for(int i=0;i<count;i++){
  uint32_t argb=((uint32_t (*)(void*,int))BfaNativeAddress(0x533e900))(provider,colors[i]);
  *(uintptr_t *)(rounded+8)=((uintptr_t)1<<32)|argb;
  ((void (*)(void*,void*,void*))BfaNativeAddress(0x2e81660))(rounded,canvas,row);
 }
 return 1;
}
NATIVE_CALLS static void paint_inset_outline(void *raw_row,void *canvas,int inset,int selected) {
 unsigned char *row=raw_row;
 void *widget=*(void **)(row+0x190);if(!widget)return;
 void *provider=((void *(*)(void*))BfaNativeAddress(0x5433810))((unsigned char *)widget+0x48);
 if(!provider)return;
 uint32_t color=((uint32_t (*)(void*,int))BfaNativeAddress(0x533e900))(provider,0xd8);
 /* Native row border PaintFlags: stroke 1.5, miter 4, antialias, native color. */
 unsigned char flags[80];for(int i=0;i<80;i++)flags[i]=0;
 float *rgba=(float *)flags;
 rgba[0]=(float)((color>>16)&255)/255.0f;rgba[1]=(float)((color>>8)&255)/255.0f;
 rgba[2]=(float)(color&255)/255.0f;rgba[3]=(float)(color>>24)/255.0f;
 *(float *)(flags+16)=selected?0.5f:1.5f;*(float *)(flags+20)=4.0f;
 *(uint64_t *)(flags+24)=0xbf80000000000341ull;
 float edge=selected?-0.25f:1.0f;
 float rect[4]={(float)inset+edge,edge,(float)*(int *)(row+0x1d0)-6.0f-edge,
                (float)*(int *)(row+0x1d4)-edge};
 void *backend=*(void **)((unsigned char *)canvas+0xc0);
 void (*draw)(void*,const float*,float,float,void*)=
     *(void (**)(void*,const float*,float,float,void*))(*(uintptr_t *)backend+0x100);
 float radius=*(float *)BfaNativeAddress(0xf99f9d8);
 draw(backend,rect,radius,radius,flags);
}
void BfaPaintInsetOutline(void *row,void *canvas,int inset) {
 paint_inset_outline(row,canvas,inset,0);
}
void BfaPaintInsetSelectedOutline(void *row,void *canvas,int inset) {
 paint_inset_outline(row,canvas,inset,1);
}
NATIVE_CALLS void BfaInstallFolderBackground(BfaOwner *owner,void *view) {
 void *prototype=0;BfaRoundedBackground(&prototype,(uintptr_t)1<<32,0,0);
 FolderBackground *self=BfaAllocate(sizeof(*self));
 self->methods=BfaAllocate(56);
 for(size_t i=0;i<7;i++)self->methods[i]=((void **)*(uintptr_t *)prototype)[i];
 /* Callbacks are GFID targets in the embedded payload. */
 self->methods[0]=(void *)destroy_folder_background;
 self->methods[4]=(void *)paint_folder_background;
 self->transparent=(uintptr_t)1<<32;self->owner=owner;BfaAddOwnerReference(owner);
 self->original=*(void **)((unsigned char *)view+0x228);
 *(void **)((unsigned char *)view+0x228)=0;
 self->rounded=0;BfaRoundedBackground(&self->rounded,(uintptr_t)1<<32,BfaRowCornerRadius,0);
 destroy_background(prototype);
 void *owned=self;BfaSetBackground(view,&owned);BfaSchedulePaint(view);
}
NATIVE_CALLS void BfaPaintFolderBlock(void *background,void *canvas,void *view,
 int x,int y,int width,int height,int color,int expanded,int hovered) {
 unsigned char *native_view=view,*rounded=background;
 void *widget=*(void **)(native_view+0x190);if(!widget)return;
 void *provider=((void *(*)(void*))BfaNativeAddress(0x5433810))((unsigned char *)widget+0x48);
 if(!provider)return;
 int id=((int (*)(int,int))BfaNativeAddress(0x35049a0))(color,1);
 uint32_t argb=((uint32_t (*)(void*,int))BfaNativeAddress(0x533e900))(provider,id);
 /* Exact native group fill: 10 percent collapsed, 20 percent expanded/hovered. */
 argb=(argb&0xffffff)|((expanded||hovered)?0x33000000:0x1a000000);
 if(color==0){
  /* Native neutral groups have no persistent colored fill. */
  argb=0;
  if(hovered){argb=((uint32_t (*)(void*,int))BfaNativeAddress(0x533e900))(provider,0xe4);argb=(argb&0xffffff)|0x0d000000;}
 }
 *(uintptr_t *)(rounded+8)=((uintptr_t)1<<32)|argb;
 /* gfx::Insets storage: top, left, bottom, right. Native painter handles AA. */
 *(int *)(rounded+0x20)=y;*(int *)(rounded+0x24)=x;
 *(int *)(rounded+0x28)=*(int *)(native_view+0x1d4)-y-height;
 *(int *)(rounded+0x2c)=*(int *)(native_view+0x1d0)-x-width;
 ((void (*)(void*,void*,void*))BfaNativeAddress(0x2e81660))(background,canvas,view);
}
