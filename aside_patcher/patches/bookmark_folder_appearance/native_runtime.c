#include "native_runtime.h"
#include "native_ui.h"
#include "native_background.h"
#define NATIVE_CALLS __declspec(guard(nocf))

typedef struct BfaRow BfaRow;
struct BfaRow {
    BfaRow *next;
    BfaRowRendering rendering;
    int base_margins[4];
    int base_has_margins, padding, requested_padding, footer;
    void *owned_margins, *footer_last;
    void *highlight,*original_highlight;
    int highlight_inset;
    int paint_x,paint_y,paint_width,paint_height,paint_state;
    int saved_selected,saved_dragged,suppressed_outline;
    int block_height,block_valid;
    int block_from_height,block_target_height,block_target_valid;
    int block_footer,block_from_footer,block_target_footer;
    int hidden_by_patch, hide_after_animation, hidden_margins;
    int base_preferred_size[3], size_overridden, size_projected;
};
struct BfaOwner {
    BfaOwner *next;
    void *native_owner;
    void *prefs;
    void *model;
    BfaRow *rows;
    void *rows_view;
    size_t references;
    int laying_out;
    int blocks_pending,blocks_captured;
    int target_prepared,measuring_target;
};
static BfaOwner *owners;

static BfaOwner *find_owner(void *native_owner) {
    for (BfaOwner *owner = owners; owner; owner = owner->next) {
        if (owner->native_owner == native_owner) return owner;
    }
    return 0;
}

static BfaRow *find_row(BfaOwner *owner,void *row) {
    BfaRow *record=owner->rows;
    while(record && record->rendering.row!=row)record=record->next;
    return record;
}

static int dragging_ancestor(BfaOwner *owner,unsigned char *node) {
 for(;node;node=*(unsigned char **)(node+0x38)){
  for(BfaRow *record=owner->rows;record;record=record->next){
   unsigned char *row=record->rendering.row;
   if(*(void **)(row+0x3f0)==node &&
      (row[0x458] || (record->suppressed_outline && record->saved_dragged)))return 1;
  }
 }
 return 0;
}

static void *row_property(unsigned char *row,size_t rva) {
    uintptr_t key=(uintptr_t)BfaNativeAddress(rva);
    uintptr_t *node=*(uintptr_t **)(row+0xe8);
    while(node && node!=(uintptr_t *)(row+0xe8)){
        if(key==node[4])return (void *)node[6];
        node=(uintptr_t *)node[key>node[4]?1:0];
    }
    return 0;
}
static int *row_margins(unsigned char *row) {return row_property(row,0x10a35cb0);}

static int folder_expanded(unsigned char *owner,unsigned char *node) {
    int64_t *ids=*(int64_t **)(owner+0x648),id=*(int64_t *)(node+0x60);
    size_t lo=0,hi=*(size_t *)(owner+0x650),count=hi;
    while(lo<hi){size_t mid=lo+(hi-lo)/2;if(ids[mid]<id)lo=mid+1;else hi=mid;}
    return lo<count && ids[lo]==id;
}

NATIVE_CALLS static void project_row_size(BfaRow *record,int hidden) {
    unsigned char *row=record->rendering.row;
    int *current=(int *)(row+0x1bc);
    if(hidden){
        if(record->size_overridden && row[0x1c4] && current[1]==0)return;
        for(int i=0;i<3;i++)record->base_preferred_size[i]=current[i];
        int size[3]={row[0x1c4]?current[0]:*(int *)(row+0x1d0),0,1};
        record->size_overridden=1;
        record->size_projected=1;
        ((void (*)(void*,void*))BfaNativeAddress(0x1e21260))(row,size);
    }else if(record->size_overridden){
        record->size_overridden=0;
        ((void (*)(void*,void*))BfaNativeAddress(0x1e21260))(row,record->base_preferred_size);
    }
}

NATIVE_CALLS static void project_folder_rows(BfaOwner *owner,void *view) {
    unsigned char *native_owner=owner->native_owner,*active=0;
    if(native_owner[0x721]==1 && native_owner[0x8f0]==1)
        active=BfaFindFolder(native_owner,*(int64_t *)(native_owner+0x8e8));
    for(BfaRow *record=owner->rows;record;record=record->next){
        unsigned char *row=record->rendering.row,*node=*(unsigned char **)(row+0x3f0);
        if(*(void **)(row+0x198)!=view || !node || dragging_ancestor(owner,node))continue;
        int visible=1;
        unsigned char *ancestor=*(unsigned char **)(node+0x38);
        for(int depth=*(int *)(row+0x3f8);depth>0 && ancestor;--depth){
            if(!folder_expanded(native_owner,ancestor)){visible=0;break;}
            ancestor=*(unsigned char **)(ancestor+0x38);
        }
        for(ancestor=active;ancestor;ancestor=*(unsigned char **)(ancestor+0x38))
            if(ancestor==node){visible=1;break;}
        /* Keep loaded native row objects and their tab metadata alive. */
        if(!visible && !record->hidden_by_patch){
            record->hidden_by_patch=1;
            record->hide_after_animation=row[0x1d8] && owner->blocks_pending && !owner->blocks_captured;
            if(!record->hide_after_animation)
                ((void (*)(void*,int))BfaNativeAddress(0x5190ec0))(row,0);
        }else if(visible && record->hidden_by_patch){
            record->hidden_by_patch=0;record->hide_after_animation=0;
            ((void (*)(void*,int))BfaNativeAddress(0x5190ec0))(row,1);
        }
        project_row_size(record,!visible);
        if(*(int *)(node+0xf8)!=0){
            int expanded=folder_expanded(native_owner,node);
            if(row[0x455]!=expanded){
                row[0x455]=(unsigned char)expanded;
                unsigned char *caret=*(unsigned char **)(row+0x440);
                if(caret && caret[0x420]!=expanded){
                    caret[0x420]=(unsigned char)expanded;
                    ((void (*)(void*,int))BfaNativeAddress(0x18ce660))(caret+0x398,!expanded);
                    BfaSchedulePaint(caret);
                }
                BfaSchedulePaint(row);
            }
        }
    }
}

void BfaBeginFolderTransition(void *native_owner,void *raw_node) {
    unsigned char *node=raw_node;
    if(!node || !*(int *)(node+0xf8))return;
    BfaOwner *owner=find_owner(native_owner);if(!owner)return;
    owner->blocks_pending=1;owner->blocks_captured=0;
    owner->target_prepared=0;
    for(BfaRow *record=owner->rows;record;record=record->next){
        record->block_from_height=record->block_height;
        record->block_from_footer=record->block_footer;record->block_target_footer=0;
        record->block_target_valid=0;
    }
}

NATIVE_CALLS void BfaCaptureFolderTargets(void *view) {
    for(BfaOwner *owner=owners;owner;owner=owner->next){
        if(owner->rows_view!=view)continue;
        if(owner->blocks_pending && !owner->blocks_captured && owner->target_prepared && !owner->laying_out){
            owner->laying_out=1;
            for(BfaRow *record=owner->rows;record;record=record->next){
                if(!record->hide_after_animation)continue;
                unsigned char *row=record->rendering.row,*node=*(unsigned char **)(row+0x3f0);
                int rect[4];for(int axis=0;axis<4;axis++)rect[axis]=((int *)(row+0x1c8))[axis];
                for(unsigned char *ancestor=node?*(unsigned char **)(node+0x38):0;ancestor;ancestor=*(unsigned char **)(ancestor+0x38)){
                    for(BfaRow *parent=owner->rows;parent;parent=parent->next){
                        unsigned char *header=parent->rendering.row;
                        if(*(void **)(header+0x3f0)==ancestor && !parent->hidden_by_patch &&
                           !folder_expanded(owner->native_owner,ancestor))rect[1]=*(int *)(header+0x1cc)+*(int *)(header+0x1d4);
                    }
                }
                rect[3]=0;
                ((void (*)(void*,void*))BfaNativeAddress(0x4e522d0))(row,rect);
            }
            BfaPaintFolderBlocks(owner,0,view,0);owner->blocks_captured=1;
            owner->laying_out=0;
        }
        return;
    }
}

void BfaFolderAnimationProgress(void *native_owner) {
    BfaOwner *owner=find_owner(native_owner);
    if(owner && owner->blocks_pending && owner->rows_view)BfaSchedulePaint(owner->rows_view);
}

static unsigned char *row_animation_entry(unsigned char *animator,void *row) {
    unsigned char *entry=*(unsigned char **)(animator+0xb8);
    while(entry && entry!=animator+0xb8){
        uintptr_t key=*(uintptr_t *)(entry+0x20);
        if(key==(uintptr_t)row)return entry;
        entry=*(unsigned char **)(entry+((uintptr_t)row>key?8:0));
    }
    return 0;
}

extern void BfaOriginalLayoutImmediately(void *);
extern void BfaOriginalRowMask(void *);

NATIVE_CALLS void BfaRowAnimationProgress(void *animator,void *animation) {
    for(BfaOwner *owner=owners;owner;owner=owner->next){
        if(*(void **)((unsigned char *)owner->native_owner+0x550)==animator && owner->rows_view){
            for(BfaRow *record=owner->rows;record;record=record->next){
                unsigned char *row=record->rendering.row;
                unsigned char *entry=(record->hide_after_animation || record->size_projected)?row_animation_entry(animator,row):0;
                if(entry && *(void **)(entry+0x48)==animation){
                    if(record->size_projected && (!*(int *)(entry+0x34) || !*(int *)(entry+0x44))){
                        BfaOriginalLayoutImmediately(row);
                        BfaOriginalRowMask(row);
                        BfaSchedulePaint(row);
                    }
                    if(record->hide_after_animation && *(int *)(row+0x1d4)==0){
                        record->hide_after_animation=0;
                        ((void (*)(void*,int))BfaNativeAddress(0x5190ec0))(row,0);
                    }
                }
            }
            BfaSchedulePaint(owner->rows_view);
        }
    }
}

static void drag_row_offset(void *raw_row,int *x,int *y) {
    unsigned char *row=raw_row;
    /* RVA 0x3888170 stores the translation and invalidates the parent itself. */
    if(row[0x457]){
        *x+=*(int *)(row+0x45c);
        *y+=*(int *)(row+0x460);
    }
}

NATIVE_CALLS static int animated_row_rect(BfaOwner *owner,void *row,int *rect,double *progress) {
    for(int i=0;i<4;i++)rect[i]=((int *)((unsigned char *)row+0x1c8))[i];
    unsigned char *animator=*(unsigned char **)((unsigned char *)owner->native_owner+0x550);
    if(!animator)return 0;
    unsigned char *entry=row_animation_entry(animator,row);
    if(entry){
            void *animation=*(void **)(entry+0x48);
            if(!animation)return 0;
            /* Same per-row animation and rectangle interpolation as RVA 0xAB05A80. */
            ((void (*)(void*,void*,void*,void*))BfaNativeAddress(0xad8c400))(animation,rect,entry+0x28,entry+0x38);
            if(progress){
                double (*value)(void*)=*(double (**)(void*))(*(uintptr_t *)animation+0x20);
                *progress=value(animation);
                if(!(*progress>=0.0 && *progress<=1.0))*progress=1.0;
            }
            return 1;
    }
    return 0;
}

NATIVE_CALLS static void *replace_highlight(unsigned char *row,void *value) {
 unsigned char *key=BfaNativeAddress(0x10a35d50);
 return ((void *(*)(void*,void*,void*,void*,void*,void*))BfaNativeAddress(0x48dd6b0))(
     row+0xd8,key,*(void **)(key+8),*(void **)(key+0x18),value,*(void **)key);
}
NATIVE_CALLS static void destroy_highlight(void *value) {
 if(value){unsigned char *key=BfaNativeAddress(0x10a35d50);((void (*)(void*))*(void **)(key+0x18))(value);}
}
NATIVE_CALLS static void refresh_highlight(BfaRow *record,int inset) {
 unsigned char *row=record->rendering.row;
 if(!inset){
  if(record->highlight){destroy_highlight(replace_highlight(row,record->original_highlight));
   record->highlight=0;record->original_highlight=0;record->highlight_inset=0;}
  return;
 }
 if(!record->highlight){
  record->original_highlight=replace_highlight(row,0);
  int margins[4]={0,inset,0,6};
  /* Same native pill generator installed by Aside's tab-group controls. */
  ((void (*)(void*,const int*))BfaNativeAddress(0xaad7ea0))(row,margins);
  record->highlight=row_property(row,0x10a35d50);
  record->highlight_inset=inset;BfaSchedulePaint(row);
 }else if(inset!=record->highlight_inset){
  int *margins=(int *)((unsigned char *)record->highlight+8);
  margins[0]=0;margins[1]=inset;margins[2]=0;margins[3]=6;
  record->highlight_inset=inset;BfaSchedulePaint(row);
 }
}

NATIVE_CALLS static void set_row_padding(BfaRow *record) {
    unsigned char *row=record->rendering.row;
    int *current=row_margins(row),hidden=record->hidden_by_patch;
    int wanted=hidden?0:record->requested_padding;
    /* Native rebuilds can clear the owned margins in place. */
    if(record->padding && (current!=record->owned_margins ||
       !current || current[2]!=record->base_margins[2]+record->padding))record->padding=0;
    if(record->hidden_margins && (current!=record->owned_margins ||
       !current || current[0] || current[1] || current[2] || current[3]))record->hidden_margins=0;
    if(!record->padding && !record->hidden_margins){
        record->base_has_margins=current!=0;
        for(int i=0;i<4;i++)record->base_margins[i]=current?current[i]:0;
    }
    if(record->padding==wanted && record->hidden_margins==hidden)return;
    int *value=0;
    if(hidden || wanted || record->base_has_margins){
        value=BfaAllocate(16);
        for(int i=0;i<4;i++)value[i]=hidden?0:record->base_margins[i];
        value[2]+=wanted;
    }
    /* Verified PropertyHandler::SetPropertyInternal six-argument ABI. */
    void *old=((void *(*)(void*,void*,void*,void*,void*,void*))BfaNativeAddress(0x48dd6b0))(
        row+0xd8,BfaNativeAddress(0x10a35cb0),BfaNativeAddress(0x10a35ec8),
        BfaNativeAddress(0x363cd0),value,0);
    if(old)((void (*)(void*))BfaNativeAddress(0x363cd0))(old);
    record->owned_margins=value;record->padding=wanted;record->hidden_margins=hidden;
    /* Native size setters invalidate this layout and recursively its parents. */
    ((void (*)(void*,int))BfaNativeAddress(0x4567210))(row,0);
}

static size_t subtree_end(BfaOwner *owner,void **children,size_t count,size_t first,const void *node) {
    size_t end=first;
    for(size_t j=first+1;j<count;j++){
        unsigned char *child=children[j];
        if(!child || *(void **)child!=BfaNativeAddress(0xfe16378))break;
        unsigned char *child_node=*(unsigned char **)(child+0x3f0);
        unsigned char *ancestor=child_node?*(unsigned char **)(child_node+0x38):0;
        while(ancestor && ancestor!=node)ancestor=*(unsigned char **)(ancestor+0x38);
        if(ancestor!=node)break;
        BfaRow *record=find_row(owner,child);
        if(child[0x1d8] && (!record || !record->hidden_by_patch))end=j;
    }
    return end;
}

static void apply_folder_spacing(BfaOwner *owner,void *raw_view) {
    if(owner->laying_out || !owner->native_owner || !owner->prefs)return;
    owner->laying_out=1;
    unsigned char *view=raw_view;
    void **children=*(void ***)(view+0x1a0);size_t count=*(size_t *)(view+0x1a8);
    /* URL favicon updates do not always traverse the folder icon hook. */
    for(size_t i=0;i<count;i++){
        unsigned char *row=children[i];
        if(row && *(void **)row==BfaNativeAddress(0xfe16378) && !find_row(owner,row))
            BfaRowUpdated(row);
    }
    project_folder_rows(owner,raw_view);
    for(BfaRow *record=owner->rows;record;record=record->next){
        record->requested_padding=0;record->footer=0;record->footer_last=0;
    }
    for(size_t i=0;i<count;i++){
        unsigned char *row=children[i];BfaRow *record=find_row(owner,row);
        if(!row || !record || !row[0x1d8] || record->hidden_by_patch)continue;
        unsigned char *node=*(unsigned char **)(row+0x3f0);
        if(!node || *(int *)(node+0xf8)==0)continue;
        BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));
        if(!BfaValidGuid(guid.data,guid.size))continue;
        BfaAppearance appearance;BfaReadAppearance(owner->prefs,guid.data,&appearance);
        if(!BfaValidColor(appearance.color))continue;
        size_t end=subtree_end(owner,children,count,i,node);
        if(end==i)continue;
        BfaRow *last=find_row(owner,children[end]);if(!last)continue;
        record->footer=1;record->footer_last=children[end];
        last->requested_padding+=4;
    }
    for(BfaRow *record=owner->rows;record;record=record->next){
        unsigned char *row=record->rendering.row;
        /* Four pixels inside each ending folder, four outside the outer one. */
        if(record->requested_padding)record->requested_padding+=4;
        if(*(void **)(row+0x198)==raw_view)set_row_padding(record);
    }
    owner->laying_out=0;
}

int BfaNestedRowInset(void *raw_row) {
    unsigned char *row=raw_row;
    int depth=*(int *)(row+0x3f8),width=*(int *)(row+0x1d0);
    if(depth<=0 || depth>=width/14)return 0;
    BfaOwner *owner=find_owner(*(void **)(row+0x3e0));
    if(!owner || !owner->prefs)return 0;
    unsigned char *node=*(unsigned char **)(row+0x3f0);
    while(node){
        if(*(int *)(node+0xf8)!=0){
            BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));
            if(BfaValidGuid(guid.data,guid.size)){
                BfaAppearance appearance;
                BfaReadAppearance(owner->prefs,guid.data,&appearance);
                if(BfaValidColor(appearance.color) || appearance.icon>=0)return depth*14;
            }
        }
        node=*(unsigned char **)(node+0x38);
    }
    return 0;
}

int BfaColoredFolder(void *raw_row) {
 unsigned char *row=raw_row,*node=*(unsigned char **)(row+0x3f0);
 if(!node || *(int *)(node+0xf8)==0)return 0;
 BfaOwner *owner=find_owner(*(void **)(row+0x3e0));if(!owner || !owner->prefs)return 0;
 BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));
 if(!BfaValidGuid(guid.data,guid.size))return 0;
 BfaAppearance appearance;BfaReadAppearance(owner->prefs,guid.data,&appearance);
 return BfaValidColor(appearance.color);
}

void BfaRowStatePainted(void *raw_row) {
 unsigned char *row=raw_row;BfaOwner *owner=find_owner(*(void **)(row+0x3e0));
 if(!owner || !owner->rows_view)return;
 BfaRow *record=find_row(owner,row);if(!record)return;
 int x=*(int *)(row+0x1c8),y=*(int *)(row+0x1cc),width=*(int *)(row+0x1d0),height=*(int *)(row+0x1d4);
 int state=row[0x1d8]|(row[0x453]<<1)|(row[0x450]<<2)|(row[0x458]<<3)|(*(int *)(row+0x464)<<4);
 if(record->paint_x==x && record->paint_y==y && record->paint_width==width &&
    record->paint_height==height && record->paint_state==state)return;
 record->paint_x=x;record->paint_y=y;record->paint_width=width;record->paint_height=height;record->paint_state=state;
 BfaSchedulePaint(owner->rows_view);
}

int BfaPaintDraggingFolder(void *raw_row,void *canvas) {
 (void)canvas;
 unsigned char *row=raw_row,*node=*(unsigned char **)(row+0x3f0);
 if(!node || *(int *)(node+0xf8)==0)return 0;
 BfaOwner *owner=find_owner(*(void **)(row+0x3e0));if(!owner || !owner->prefs || !owner->rows_view)return 0;
 BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));if(!BfaValidGuid(guid.data,guid.size))return 0;
 BfaAppearance appearance;BfaReadAppearance(owner->prefs,guid.data,&appearance);
 if(!BfaValidColor(appearance.color))return 0;
 /* The shared container paints the complete block, outside the row layer's clip. */
 return 1;
}

void BfaRefreshRowMask(void *raw_row,int force) {
 unsigned char *row=raw_row;
 BfaOwner *owner=find_owner(*(void **)(row+0x3e0));if(!owner)return;
 BfaRow *record=find_row(owner,row);if(!record)return;
 if(force && owner->rows_view)BfaSchedulePaint(owner->rows_view);
 refresh_highlight(record,BfaNestedRowInset(row));
}

void BfaSuppressContainerOutline(void *view,int suppress) {
 for(BfaOwner *owner=owners;owner;owner=owner->next){
  if(owner->rows_view!=view)continue;
  for(BfaRow *record=owner->rows;record;record=record->next){
   unsigned char *row=record->rendering.row;
   if(suppress){
    if(!BfaNestedRowInset(row))continue;
    record->saved_selected=row[0x450];record->saved_dragged=row[0x458];
    record->suppressed_outline=1;row[0x450]=0;row[0x458]=0;
   }else if(record->suppressed_outline){
    row[0x450]=(unsigned char)record->saved_selected;row[0x458]=(unsigned char)record->saved_dragged;
    record->suppressed_outline=0;
   }
  }
  return;
 }
}

BfaOwner *BfaRetainOwner(void *native_owner) {
    BfaOwner *owner = find_owner(native_owner);
    if (!owner) {
        void *prefs = BfaProfilePrefs(native_owner);
        if (!prefs) return 0;
        owner = BfaAllocate(sizeof(*owner));
        owner->native_owner = native_owner;
        owner->prefs = prefs;
        owner->model = *(void **)((unsigned char *)native_owner + 0x540);
        owner->rows = 0;
        owner->rows_view = 0;
        owner->references = 1; /* Registry ownership until the native destructor. */
        owner->laying_out=0;
        owner->blocks_pending=0;owner->blocks_captured=0;
        owner->target_prepared=0;owner->measuring_target=0;
        BfaRegisterPreference(owner->prefs);
        owner->next = owners;
        owners = owner;
    }
    ++owner->references;
    void *view = *(void **)((unsigned char *)native_owner + 0x548);
    if (view && (view != owner->rows_view || !BfaHasFolderBackground(view))) {
        owner->rows_view = view;
        BfaInstallFolderBackground(owner, view);
    }
    return owner;
}

void BfaAddOwnerReference(BfaOwner *owner) { ++owner->references; }

void BfaPrepareFolderPaint(void *view) {
    for (BfaOwner *owner = owners; owner; owner = owner->next) {
        if (owner->rows_view == view) {
            if(owner->blocks_pending && !owner->target_prepared && !owner->measuring_target)return;
            apply_folder_spacing(owner,view);
            for(BfaRow *record=owner->rows;record;record=record->next)BfaRefreshRowMask(record->rendering.row,0);
            if(!BfaHasFolderBackground(view))BfaInstallFolderBackground(owner, view);
            return;
        }
    }
}

void BfaPrepareFolderMeasurement(void *view,int target) {
    for(BfaOwner *owner=owners;owner;owner=owner->next){
        if(owner->rows_view!=view)continue;
        if(target)owner->measuring_target=1;
        BfaPrepareFolderPaint(view);
        if(target){owner->measuring_target=0;owner->target_prepared=1;}
        return;
    }
}

void BfaFinishFolderUpdate(void *native_owner) {
    BfaOwner *owner=find_owner(native_owner);
    if(owner && owner->blocks_pending && !owner->target_prepared){
        owner->blocks_pending=0;owner->blocks_captured=0;
        if(owner->rows_view)BfaPrepareFolderPaint(owner->rows_view);
    }
}

void BfaReleaseOwner(BfaOwner *owner) {
    if (owner && --owner->references == 0) BfaFree(owner, sizeof(*owner));
}

void *BfaOwnerPointer(BfaOwner *owner) {
    return owner ? owner->native_owner : 0;
}

static void render_row(BfaOwner *owner, BfaRow *record) {
    unsigned char *row = record->rendering.row;
    unsigned char *node = *(unsigned char **)(row + 0x3f0);
    if (!node || *(int *)(node + 0xf8) == 0) return;
    BfaStringView guid = BfaStringBytes((const BfaString *)(node + 0x68));
    if (!BfaValidGuid(guid.data, guid.size)) return;
    BfaAppearance appearance;
    BfaReadAppearance(owner->prefs, guid.data, &appearance);
    BfaRenderRow(&record->rendering, appearance);
}

void BfaRowUpdated(void *row) {
    unsigned char *node = *(unsigned char **)((unsigned char *)row + 0x3f0);
    if (!node) return;
    void *native_owner = *(void **)((unsigned char *)row + 0x3e0);
    BfaOwner *owner = BfaRetainOwner(native_owner);
    if (!owner) return;
    BfaRow *record = owner->rows;
    while (record && record->rendering.row != row) record = record->next;
    if (!record) {
        record = BfaAllocate(sizeof(*record));
        for(size_t i=0;i<sizeof(*record);i++)((unsigned char *)record)[i]=0;
        record->rendering.row = row;
        record->next = owner->rows;
        owner->rows = record;
    }
    render_row(owner, record);
    if(owner->rows_view)BfaSchedulePaint(owner->rows_view);
    BfaReleaseOwner(owner);
}

void BfaRowDestroyed(void *row) {
    BfaCancelRowMenus(row);
    for (BfaOwner *owner = owners; owner; owner = owner->next) {
        BfaRow **link = &owner->rows;
        while (*link) {
            BfaRow *record = *link;
            if (record->rendering.row == row) {
                *link = record->next;
                BfaReleaseRendering(&record->rendering);
                destroy_highlight(record->original_highlight);
                BfaFree(record, sizeof(*record));
                return;
            }
            link = &record->next;
        }
    }
}

void BfaOwnerDestroyed(void *native_owner) {
    BfaCancelOwnerMenus(native_owner);
    BfaOwner **link = &owners;
    while (*link) {
        BfaOwner *owner = *link;
        if (owner->native_owner == native_owner) {
            *link = owner->next;
            owner->native_owner = 0; /* Outstanding menu callbacks become inert. */
            owner->prefs = 0;
            owner->model = 0;
            BfaRow *record = owner->rows;
            owner->rows = 0;
            while (record) {
                BfaRow *next = record->next;
                BfaReleaseRendering(&record->rendering);
                destroy_highlight(record->original_highlight);
                BfaFree(record, sizeof(*record));
                record = next;
            }
            BfaReleaseOwner(owner);
            return;
        }
        link = &owner->next;
    }
}

int BfaOwnerAppearance(BfaOwner *owner, int64_t identifier,
                       const char guid[36], BfaAppearance *appearance) {
    if (!owner || !owner->native_owner || !BfaValidGuid(guid, 36)) return 0;
    unsigned char *node = BfaFindFolder(owner->native_owner, identifier);
    if (!node || *(int *)(node + 0xf8) == 0 || node[0x138]) return 0;
    BfaStringView actual = BfaStringBytes((const BfaString *)(node + 0x68));
    if (!BfaValidGuid(actual.data, actual.size)) return 0;
    for (size_t i = 0; i < 36; ++i) if (actual.data[i] != guid[i]) return 0;
    BfaReadAppearance(owner->prefs, guid, appearance);
    return 1;
}

int BfaSetOwnerAppearance(BfaOwner *owner, int64_t identifier,
                          const char guid[36], BfaAppearance appearance) {
    BfaAppearance previous;
    if (!BfaOwnerAppearance(owner, identifier, guid, &previous)) return 0;
    BfaWriteAppearance(owner->prefs, guid, appearance);
    for (BfaOwner *window = owners; window; window = window->next) {
        if (window->prefs != owner->prefs) continue;
        for (BfaRow *record = window->rows; record; record = record->next) render_row(window, record);
        if (window->rows_view) BfaSchedulePaint(window->rows_view);
    }
    return 1;
}

/* Layout stays native. Paint one rounded block over each visible subtree. */
NATIVE_CALLS void BfaPaintFolderBlocks(BfaOwner *owner, void *canvas, void *view, void *background) {
    unsigned char *container = view;
    void **children = *(void ***)(container + 0x1a0);
    size_t count = *(size_t *)(container + 0x1a8);
    for (size_t i = 0; i < count; ++i) {
        unsigned char *row = children[i];
        if(!row || !row[0x1d8])continue;
        BfaRow *record = owner->rows;
        while (record && record->rendering.row != row) record = record->next;
        if (!record) continue;
        if(!canvas && record->hidden_by_patch)continue;
        unsigned char *node = *(unsigned char **)(row + 0x3f0);
        if (!node || *(int *)(node + 0xf8) == 0) continue;
        int dragging=dragging_ancestor(owner,node);
        BfaStringView guid = BfaStringBytes((BfaString *)(node + 0x68));
        if (!BfaValidGuid(guid.data, guid.size)) continue;
        BfaAppearance appearance;
        BfaReadAppearance(owner->prefs, guid.data, &appearance);
        if (!BfaValidColor(appearance.color)) continue;
        int rect[4];
        for(int axis=0;axis<4;axis++)rect[axis]=((int *)(row+0x1c8))[axis];
        if(canvas && !dragging)animated_row_rect(owner,row,rect,0);
        int x=rect[0],y=rect[1],width=rect[2],bottom=y+rect[3];
        int animated_children=0;double child_progress=1.0;
        /* Constructor and caret update use +0x455 for native expansion state. */
        int expanded = row[0x455] != 0;
        for (size_t j = i + 1; j < count; ++j) {
            unsigned char *child = children[j];
            if (!child || *(void **)child != BfaNativeAddress(0xfe16378)) break;
            unsigned char *child_node = *(unsigned char **)(child + 0x3f0);
            unsigned char *ancestor = child_node ? *(unsigned char **)(child_node + 0x38) : 0;
            while (ancestor && ancestor != node) ancestor = *(unsigned char **)(ancestor + 0x38);
            if (ancestor != node) break;
            if(!child[0x1d8])continue;
            BfaRow *child_record=find_row(owner,child);
            if(!canvas && child_record && child_record->hidden_by_patch)continue;
            int child_rect[4];
            for(int axis=0;axis<4;axis++)child_rect[axis]=((int *)(child+0x1c8))[axis];
            if(canvas && !dragging && animated_row_rect(owner,child,child_rect,&child_progress))animated_children=1;
            if(child_rect[3]<=0)continue;
            expanded=1;
            int end=child_rect[1]+child_rect[3];
            if (end > bottom) bottom = end;
        }
        if (width > 0 && bottom > y) {
            /* Match the row's native 14-pixel indentation at every depth. */
            int depth = *(int *)(row + 0x3f8);
            if (depth > 0) {
                if (depth > width / 14) continue;
                x += depth * 14;
                width -= depth * 14 + 6;
                if(width<=0)continue;
            }
            int footer=0;
            BfaRow *last=find_row(owner,record->footer_last);
            if(record->footer && last){
                footer=last->requested_padding-4;
                unsigned char *ancestor=*(unsigned char **)(node+0x38);
                while(ancestor){
                    for(BfaRow *parent=owner->rows;parent;parent=parent->next){
                        unsigned char *parent_row=parent->rendering.row;
                        if(*(void **)(parent_row+0x3f0)==ancestor && parent->footer &&
                           parent->footer_last==record->footer_last)footer-=4;
                    }
                    ancestor=*(unsigned char **)(ancestor+0x38);
                }
            }
            int height=bottom-y+footer;
            if(height<=0)continue;
            if(!canvas){
                record->block_target_height=height;
                record->block_target_footer=footer;
                record->block_target_valid=1;continue;
            }
            unsigned char *native_owner=owner->native_owner;
            if(!dragging && animated_children && owner->blocks_pending && owner->blocks_captured){
                footer=record->block_from_footer+(int)((record->block_target_footer-record->block_from_footer)*child_progress);
                height=bottom-y+footer;
            }else if(!dragging && owner->blocks_pending && owner->blocks_captured && native_owner &&
               native_owner[0x568] && record->block_valid && record->block_target_valid){
                /* Native height animation uses this same eased value at RVA 0x3886500. */
                double progress=*(double *)(native_owner+0x5c8);
                if(!(progress>=0.0 && progress<=1.0))progress=1.0;
                height=record->block_from_height+(int)((record->block_target_height-record->block_from_height)*progress);
                if(height>*(int *)(row+0x1d4))expanded=1;
            }
            if(dragging)drag_row_offset(row,&x,&y);
            record->block_height=height;record->block_footer=footer;record->block_valid=1;
            BfaPaintFolderBlock(background, canvas, view, x, y, width, height,
                                appearance.color, expanded, row[0x453] || row[0x450]);
        }
    }
    unsigned char *animator=owner->native_owner?*(unsigned char **)((unsigned char *)owner->native_owner+0x550):0;
    if(canvas && owner->blocks_captured && owner->native_owner && !((unsigned char *)owner->native_owner)[0x568] &&
       (!animator || !*(size_t *)(animator+0xc0))){
        owner->blocks_pending=0;owner->blocks_captured=0;
    }
}

void BfaModelRemoving(void *model, const void *node) {
    for (BfaOwner *owner = owners; owner; owner = owner->next) {
        if (owner->model == model) {
            BfaRemoveSubtreeAppearance(owner->prefs, node);
            return; /* Windows sharing a BookmarkModel share this preference. */
        }
    }
}
