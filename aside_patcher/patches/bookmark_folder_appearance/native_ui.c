#include "native_ui.h"
#include <intrin.h>
#include <wchar.h>
#define NATIVE_CALLS __declspec(guard(nocf))
extern const unsigned BfaNativeTlsIndex;

struct BfaMenu {
    BfaMenu *next;
    BfaOwner *owner;
    void *row;
    char guid[36];
    int64_t identifier, generation;
    size_t references;
    int closing, command;
    void *popup, *widget, *picker_popup, *picker_widget;
    uintptr_t colors[8];
    int origin[4];
};
typedef struct {
    BfaBindState base;
    BfaMenu *menu;
    void *widget, *popup;
    int main;
} CloseState;
typedef struct {BfaBindState base; BfaMenu *menu;} IconState;
static BfaMenu *menus;

static void zero(void *data, size_t size) {
    for(size_t i=0;i<size;i++)((unsigned char *)data)[i]=0;
}
void BfaRetainMenu(BfaMenu *menu) {++menu->references;}
static void release_menu(BfaMenu *menu) {
    if(--menu->references==0){BfaReleaseOwner(menu->owner);BfaFree(menu,sizeof(*menu));}
}
int BfaQueryState(void *state,int query) {(void)state;(void)query;return 0;}
void BfaDestroyAction(void *raw) {
    BfaActionState *state=raw;release_menu(state->menu);BfaFree(state,sizeof(*state));
}
static void destroy_icon(void *raw) {
    IconState *state=raw;release_menu(state->menu);BfaFree(state,sizeof(*state));
}
static void destroy_close(void *raw) {
    CloseState *state=raw;release_menu(state->menu);BfaFree(state,sizeof(*state));
}
NATIVE_CALLS static void close_widget(void *widget) {
    if(widget)((void (*)(void*,int))BfaNativeAddress(0x20d57f0))(widget,5);
}
static void close_menu(BfaMenu *menu) {
    if(menu->closing)return;
    close_widget(menu->picker_widget);
    close_widget(menu->widget);
}

NATIVE_CALLS static void cleanup(CloseState *state) {
    BfaMenu *menu=state->menu;
    void (*destroy)(void*,unsigned)=*(void (**)(void*,unsigned))*(uintptr_t *)state->widget;
    destroy(state->widget,1);
    ((void (*)(void*))BfaNativeAddress(0xaafb850))(state->popup);
    BfaFree(state->popup,0x450);
    if(state->main){
        BfaMenu **link=&menus;while(*link && *link!=menu)link=&(*link)->next;
        if(*link){*link=menu->next;release_menu(menu);}
        unsigned char *owner=BfaOwnerPointer(menu->owner);
        if(owner && *(int64_t *)(owner+0x9e0)==menu->generation){
            *(int *)(owner+0x9f0)=menu->command;
            ((void (*)(void*,int64_t))BfaNativeAddress(0x388ef20))(owner,menu->generation);
        }
    }
}
NATIVE_CALLS static void closing(CloseState *state) {
    BfaMenu *menu=state->menu;
    if(state->main){menu->closing=1;menu->widget=0;menu->popup=0;close_widget(menu->picker_widget);}
    else {menu->picker_widget=0;menu->picker_popup=0;}
    CloseState *task=BfaAllocate(sizeof(*task));*task=*state;
    task->base.references=1;task->base.invoke=(void (*)(void))cleanup;BfaRetainMenu(menu);
    uintptr_t *tls=(uintptr_t *)__readgsqword(0x58);
    uintptr_t storage=tls[BfaNativeTlsIndex];
    uintptr_t *scope=*(uintptr_t **)(storage+0x3d0);
    /* This callback always runs on the browser UI sequence established by Views. */
    void *runner=(void *)*scope;
    unsigned char location[32]={0};
    ((void (*)(void*,const char*,const char*,int))BfaNativeAddress(0x414f0b0))(location,"closing","bookmark_folder_appearance",1);
    int (*post)(void*,void*,void**,int64_t)=*(int (**)(void*,void*,void**,int64_t))*(uintptr_t *)runner;
    post(runner,location,(void **)&task,0);
}
NATIVE_CALLS static void popup(BfaMenu *menu,void *view,void *anchor,int main) {
    void *delegate=BfaAllocate(0x450);int rect[4]={0};
    if(main){rect[0]=menu->origin[0];rect[1]=menu->origin[1];}
    ((void (*)(void*,void*,void*,void*,unsigned,unsigned,int,int))BfaNativeAddress(0xba74c40))(delegate,anchor,0,rect,main?0:4,main?0:6,main,main);
    void *content=view;
    ((void (*)(void*,void**))BfaNativeAddress(0xaa75d50))(delegate,&view);
    CloseState *state=BfaAllocate(sizeof(*state));zero(state,sizeof(*state));
    state->base.references=1;state->base.invoke=(void (*)(void))closing;state->base.destroy=destroy_close;state->base.query=BfaQueryState;
    state->menu=menu;state->popup=delegate;state->main=main;BfaRetainMenu(menu);
    void *callback=state,*widget=0;
    ((void (*)(void**,void*,void**))BfaNativeAddress(0xaafc1c0))(&widget,delegate,&callback);
    state->widget=widget;
    if(main){menu->widget=widget;menu->popup=delegate;}
    else {menu->picker_widget=widget;menu->picker_popup=delegate;}
    ((void (*)(void*,void*,void*))BfaNativeAddress(0x369a770))(widget,delegate,content);
    ((void (*)(void*))BfaNativeAddress(0x276bd50))(widget);
}
static int appearance(BfaMenu *menu,BfaAppearance *value) {
    return !menu->closing && BfaOwnerAppearance(menu->owner,menu->identifier,menu->guid,value);
}
NATIVE_CALLS static void pick_icon(IconState *state,BfaString *owned) {
    BfaMenu *menu=state->menu;BfaAppearance value;
    BfaStringView key=BfaStringBytes(owned);
    if(appearance(menu,&value)){
        value.icon=BfaFindIcon(key.data,key.size);
        BfaSetOwnerAppearance(menu->owner,menu->identifier,menu->guid,value);
        close_menu(menu);
    }
    if(owned->bytes[23]&128){size_t capacity=*(size_t *)(owned->bytes+16)&(SIZE_MAX>>1);BfaFree(*(void **)owned->bytes,capacity);}
}
NATIVE_CALLS static void picker(BfaMenu *menu,void *anchor,BfaAppearance value) {
    if(menu->picker_widget)return;
    IconState *state=BfaAllocate(sizeof(*state));zero(state,sizeof(*state));
    state->base.references=1;state->base.invoke=(void (*)(void))pick_icon;state->base.destroy=destroy_icon;state->base.query=BfaQueryState;
    state->menu=menu;BfaRetainMenu(menu);
    BfaStringView selected={"",0};
    if(value.icon>=0 && value.icon<48){selected.data=BfaNativeIcons[value.icon].key;selected.size=BfaNativeIcons[value.icon].key_size;}
    void *view=BfaNewView(800);
    ((void (*)(void*,BfaStringView*,void*))BfaNativeAddress(0x37813b0))(view,&selected,state);
    popup(menu,view,anchor,0);
}
NATIVE_CALLS void BfaEditorAction(BfaActionState *state,void *event) {
    (void)event;BfaMenu *menu=state->menu;BfaAppearance value;
    if(!appearance(menu,&value))return;
    if(state->action>=101 && state->action<=103){menu->command=state->action-100;close_menu(menu);return;}
    if(state->action==-2){picker(menu,(void *)state->anchor,value);return;}
    if(state->action==-3){value.color=-1;value.icon=-1;}else value.color=state->action;
    if(!BfaSetOwnerAppearance(menu->owner,menu->identifier,menu->guid,value))return;
    for(int i=0;i<8;i++)if(menu->colors[i]){
        *(unsigned char *)(menu->colors[i]+0x4d0)=value.color==BfaNativeColors[i] || (value.color==-1 && BfaNativeColors[i]==0);
        ((void (*)(void*))BfaNativeAddress(0xba74c10))((void *)menu->colors[i]);
        BfaSchedulePaint((void *)menu->colors[i]);
    }
}
NATIVE_CALLS void BfaEditorHover(BfaActionState *state,void *view) {
    (void)view;BfaAppearance value;BfaMenu *menu=state->menu;
    if(!appearance(menu,&value))return;
    if(state->action==-2)picker(menu,(void *)state->anchor,value);
    else close_widget(menu->picker_widget);
}
NATIVE_CALLS int BfaShowFolderMenu(void *raw_owner,void *raw_row,const void *event) {
    unsigned char *owner=raw_owner,*row=raw_row;
    unsigned char *node=*(unsigned char **)(row+0x3f0);
    if(!node || *(int *)(node+0xf8)==0 || node[0x138])return 0;
    if(*(void **)(owner+0x6c8) || *(void **)(owner+0x6d0))return 0;
    BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));
    if(!BfaValidGuid(guid.data,guid.size))return 0;
    BfaCancelOwnerMenus(owner);
    BfaOwner *context=BfaRetainOwner(owner);
    if(!context)return 0;
    BfaMenu *menu=BfaAllocate(sizeof(*menu));zero(menu,sizeof(*menu));
    /* Observed MousePressed events use type 1 at +8. Keyboard keeps row anchoring.
       Native cursor conversion uses the nearest display's DIP coordinates.
       Instruction inspection confirms its unused first argument and Point out. */
    if(event && *(const int *)((const unsigned char *)event+8)==1){
        ((void *(*)(void *,int *))BfaNativeAddress(0xef5d80))(0,menu->origin);
    }
    menu->references=1;menu->owner=context;menu->row=row;
    for(size_t i=0;i<36;i++)menu->guid[i]=guid.data[i];
    menu->identifier=*(int64_t *)(node+0x60);
    *(int64_t *)(owner+0x9c0)=menu->identifier;owner[0x9c8]=1;
    menu->generation=++*(int64_t *)(owner+0x9e0);*(int *)(owner+0x9f0)=0;
    menu->next=menus;menus=menu;
    void *view=BfaBoxView(0),*colors=BfaBoxView(1);
    BfaAppearance value;BfaOwnerAppearance(menu->owner,menu->identifier,menu->guid,&value);
    for(int i=0;i<8;i++){
        void *button=BfaColorButton(menu,BfaNativeColors[i]);menu->colors[i]=(uintptr_t)button;
        *((unsigned char *)button+0x4d0)=value.color==BfaNativeColors[i] || (value.color==-1 && BfaNativeColors[i]==0);
        ((void (*)(void*))BfaNativeAddress(0xba74c10))(button);BfaAddChild(colors,button);
    }
    BfaAddChild(view,colors);
    BfaAddChild(view,BfaActionButton(menu,(const uint16_t *)L"Change icon",-2,1));
    BfaAddChild(view,BfaActionButton(menu,(const uint16_t *)L"Reset appearance",-3,0));
    BfaAddChild(view,BfaActionButton(menu,(const uint16_t *)L"Rename",101,0));
    BfaAddChild(view,BfaActionButton(menu,(const uint16_t *)L"Delete",102,0));
    BfaAddChild(view,BfaActionButton(menu,(const uint16_t *)L"Manage Bookmarks",103,0));
    popup(menu,view,row,1);return 1;
}
void BfaCancelOwnerMenus(void *owner) {
    for(BfaMenu *menu=menus;menu;menu=menu->next)if(BfaOwnerPointer(menu->owner)==owner){menu->command=0;close_menu(menu);}
}
void BfaCancelRowMenus(void *row) {
    for(BfaMenu *menu=menus;menu;menu=menu->next)if(menu->row==row){menu->command=0;close_menu(menu);}
}
