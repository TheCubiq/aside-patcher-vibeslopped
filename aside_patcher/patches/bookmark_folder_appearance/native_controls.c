#include "native_controls.h"
#if defined(_MSC_VER)
#define NATIVE_CALLS __declspec(guard(nocf))
#else
#define NATIVE_CALLS
#endif

extern const unsigned char BfaImageBase[];
void *BfaNativeAddress(size_t rva) {return (void *)(BfaImageBase+rva);}

NATIVE_CALLS void *BfaNewView(size_t size) {
 void *allocator=*(void **)BfaNativeAddress(0x11bd2300);if(!allocator)allocator=((void *(*)(void))BfaNativeAddress(0xbca8b20))();
 return ((void *(*)(void*,size_t,int))BfaNativeAddress(0x40232c0))(allocator,size,0);
}
NATIVE_CALLS static BfaActionState *new_action(BfaMenu *menu, int action) {
 BfaActionState *state=((void *(*)(size_t))BfaNativeAddress(0x5df7be0))(sizeof(BfaActionState));
 for(size_t i=0;i<sizeof(*state);i++)((unsigned char *)state)[i]=0;state->base.references=1;state->base.invoke=(void (*)(void))BfaEditorAction;state->base.destroy=BfaDestroyAction;state->base.query=BfaQueryState;state->action=action;state->menu=menu;BfaRetainMenu(menu);return state;
}
NATIVE_CALLS void *BfaBoxView(int horizontal) {
 void *view=BfaNewView(800);((void (*)(void*))BfaNativeAddress(0x6e2cd0))(view);
 size_t size=0x130;
 unsigned char *layout=((void *(*)(size_t))BfaNativeAddress(0x5df7be0))(size);
 ((void (*)(void*))BfaNativeAddress(0x8f70f0))(layout);
 *(void **)(layout)=BfaNativeAddress(0xfb322b0);*(void **)(layout+0x30)=BfaNativeAddress(0xfb32370);
 for(size_t i=0;i<0x30;i++)layout[0x100+i]=0;*(int *)(layout+0x100)=horizontal?0:1;
 *(int *)(layout+0x11c)=3;*(int *)(layout+0x120)=1;
 ((void (*)(void*,void**))BfaNativeAddress(0x9b4820))(view,(void **)&layout);
 return view;
}
NATIVE_CALLS void BfaAddChild(void *view,void *child) {
 ((void *(*)(void*,void*,size_t))BfaNativeAddress(0x5192d50))(view,child,*(size_t *)((uintptr_t)view+0x1a8));
}
NATIVE_CALLS void *BfaColorButton(BfaMenu *menu, int color) {
 uintptr_t button=(uintptr_t)BfaNewView(0x4d8);
 BfaActionState *state=new_action(menu,color);
 struct {void *state;uint64_t valid;} callback={state,1};
 ((void (*)(void*,void*))BfaNativeAddress(0x6e15b0))((void *)button,&callback);
 const size_t offsets[]={0,8,16,0x68,0x70,0xb0,0xd8,0x100,0x320,0x328,0x330};
 const size_t tables[]={0xfdf7f98,0xfb5e328,0xfb5e340,0xfb5e360,0xfb5e380,0xfb5e3c0,0xfb5e410,0xfb5e428,0xfb5e440,0xfb5e470,0xfb5e520};
 for(int i=0;i<11;i++)*(void **)(button+offsets[i])=BfaNativeAddress(tables[i]);
 *(int *)(button+0x4c0)=color;*(void **)(button+0x4c8)=0;*(char *)(button+0x4d0)=0;
 struct {int width,height;unsigned char valid;} preferred={24,24,1};
 ((void (*)(void*,void*))BfaNativeAddress(0x1e21260))((void *)button,&preferred);
 ((void (*)(void*,int))BfaNativeAddress(0x6e1d90))((void *)button,1);
 *(unsigned char *)(button+0x479)=1;*(void **)(button+0x458)=0;
 ((void (*)(void*))BfaNativeAddress(0x6e29b0))((void *)button);
 void *a11y=((void *(*)(void*))BfaNativeAddress(0x4c42d40))((void *)button);
 uintptr_t *names=((uintptr_t *(*)(void))BfaNativeAddress(0xb6e0de0))();
 for(size_t i=0;i<names[1];i++){
  uintptr_t entry=names[0]+i*32;
  if(*(int *)entry==color){
   const uint16_t *text=(const uint16_t *)(entry+8);size_t count=*(unsigned char *)(entry+31);
   if((signed char)*(unsigned char *)(entry+31)<0){text=*(const uint16_t **)(entry+8);count=*(size_t *)(entry+16);}
   unsigned char label[24]={0};((void (*)(void*,const uint16_t*,size_t))BfaNativeAddress(0x4028850))(label,text,count);
   ((void (*)(void*,void*))BfaNativeAddress(0x6d8d20))(a11y,label);break;
  }
 }
 ((void (*)(void*,int))BfaNativeAddress(0x6dfe40))(a11y,0x8d);
 ((void (*)(void*))BfaNativeAddress(0xba74c10))((void *)button);
 return (void *)button;
}
NATIVE_CALLS void *BfaActionButton(BfaMenu *menu,const uint16_t *label,int action,int arrow) {
 void *button=BfaNewView(0x4d0);
 BfaActionState *state=new_action(menu,action);state->anchor=(uintptr_t)button;
 BfaActionState *hover=new_action(menu,action);hover->anchor=(uintptr_t)button;
 hover->base.invoke=(void (*)(void))BfaEditorHover;
 struct {void *state;uint64_t valid;} callback={state,1};
 unsigned char text[24]={0},image[64]={0};
 /* Icons used by the native group menu, verified at its ImageModel calls. */
 size_t glyph=action==-2?0x10c6aa10:action==101?0x10560a28:action==102?0x10563ab8:action==103?0x1055f920:action==104?0x10c6a378:0x1055f3b8;
 BfaImageModel(image,BfaNativeAddress(glyph),0xde,16,0);
 /* Native action constructor takes optional<ImageModel>, present byte at +40. */
 image[40]=1;
 ((void (*)(void*,const uint16_t*,size_t))BfaNativeAddress(0x4028850))(text,label,BfaLabelSize(label));
 ((void (*)(void*,void*,void*,int,void*,void*))BfaNativeAddress(0x369c210))(button,text,image,arrow,&callback,hover);
 return button;
}

size_t BfaLabelSize(const uint16_t *label) {
 size_t size=0;while(label[size])++size;return size;
}
