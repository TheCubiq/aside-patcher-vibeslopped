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

static int *row_margins(unsigned char *row) {
    uintptr_t key=(uintptr_t)BfaNativeAddress(0x10a35cb0);
    uintptr_t *node=*(uintptr_t **)(row+0xe8);
    while(node && node!=(uintptr_t *)(row+0xe8)){
        if(key==node[4])return (int *)node[6];
        node=(uintptr_t *)node[key>node[4]?1:0];
    }
    return 0;
}

NATIVE_CALLS static void set_row_padding(BfaRow *record) {
    unsigned char *row=record->rendering.row;
    int *current=row_margins(row),wanted=record->requested_padding;
    if(record->padding && current!=record->owned_margins)record->padding=0;
    if(!record->padding){
        record->base_has_margins=current!=0;
        for(int i=0;i<4;i++)record->base_margins[i]=current?current[i]:0;
    }
    if(record->padding==wanted)return;
    int *value=0;
    if(wanted || record->base_has_margins){
        value=BfaAllocate(16);
        for(int i=0;i<4;i++)value[i]=record->base_margins[i];
        value[2]+=wanted;
    }
    /* Verified PropertyHandler::SetPropertyInternal six-argument ABI. */
    void *old=((void *(*)(void*,void*,void*,void*,void*,void*))BfaNativeAddress(0x48dd6b0))(
        row+0xd8,BfaNativeAddress(0x10a35cb0),BfaNativeAddress(0x10a35ec8),
        BfaNativeAddress(0x363cd0),value,0);
    if(old)((void (*)(void*))BfaNativeAddress(0x363cd0))(old);
    record->owned_margins=value;record->padding=wanted;
    /* Native size setters invalidate this layout and recursively its parents. */
    ((void (*)(void*,int))BfaNativeAddress(0x4567210))(row,0);
}

static size_t subtree_end(void **children,size_t count,size_t first,const void *node) {
    size_t end=first;
    for(size_t j=first+1;j<count;j++){
        unsigned char *child=children[j];
        if(!child || *(void **)child!=BfaNativeAddress(0xfe16378))break;
        unsigned char *child_node=*(unsigned char **)(child+0x3f0);
        unsigned char *ancestor=child_node?*(unsigned char **)(child_node+0x38):0;
        while(ancestor && ancestor!=node)ancestor=*(unsigned char **)(ancestor+0x38);
        if(ancestor!=node)break;
        end=j;
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
    for(BfaRow *record=owner->rows;record;record=record->next){
        record->requested_padding=0;record->footer=0;record->footer_last=0;
    }
    for(size_t i=0;i<count;i++){
        unsigned char *row=children[i];BfaRow *record=find_row(owner,row);
        if(!row || !record)continue;
        unsigned char *node=*(unsigned char **)(row+0x3f0);
        if(!node || *(int *)(node+0xf8)==0)continue;
        BfaStringView guid=BfaStringBytes((BfaString *)(node+0x68));
        if(!BfaValidGuid(guid.data,guid.size))continue;
        BfaAppearance appearance;BfaReadAppearance(owner->prefs,guid.data,&appearance);
        if(!BfaValidColor(appearance.color))continue;
        size_t end=subtree_end(children,count,i,node);
        if(!row[0x455] && end==i)continue;
        BfaRow *last=find_row(owner,children[end]);if(!last)continue;
        record->footer=1;record->footer_last=children[end];
        last->requested_padding+=6;
    }
    for(BfaRow *record=owner->rows;record;record=record->next){
        unsigned char *row=record->rendering.row;
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
            apply_folder_spacing(owner,view);
            if(!BfaHasFolderBackground(view))BfaInstallFolderBackground(owner, view);
            return;
        }
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
void BfaPaintFolderBlocks(BfaOwner *owner, void *canvas, void *view, void *background) {
    unsigned char *container = view;
    void **children = *(void ***)(container + 0x1a0);
    size_t count = *(size_t *)(container + 0x1a8);
    for (size_t i = 0; i < count; ++i) {
        unsigned char *row = children[i];
        BfaRow *record = owner->rows;
        while (record && record->rendering.row != row) record = record->next;
        if (!record) continue;
        unsigned char *node = *(unsigned char **)(row + 0x3f0);
        if (!node || *(int *)(node + 0xf8) == 0) continue;
        BfaStringView guid = BfaStringBytes((BfaString *)(node + 0x68));
        if (!BfaValidGuid(guid.data, guid.size)) continue;
        BfaAppearance appearance;
        BfaReadAppearance(owner->prefs, guid.data, &appearance);
        if (!BfaValidColor(appearance.color)) continue;
        int x = *(int *)(row + 0x1c8), y = *(int *)(row + 0x1cc);
        int width = *(int *)(row + 0x1d0), bottom = y + *(int *)(row + 0x1d4);
        /* Constructor and caret update use +0x455 for native expansion state. */
        int expanded = row[0x455] != 0;
        for (size_t j = i + 1; j < count; ++j) {
            unsigned char *child = children[j];
            if (!child || *(void **)child != BfaNativeAddress(0xfe16378)) break;
            unsigned char *child_node = *(unsigned char **)(child + 0x3f0);
            unsigned char *ancestor = child_node ? *(unsigned char **)(child_node + 0x38) : 0;
            while (ancestor && ancestor != node) ancestor = *(unsigned char **)(ancestor + 0x38);
            if (ancestor != node) break;
            expanded = 1;
            int end = *(int *)(child + 0x1cc) + *(int *)(child + 0x1d4);
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
                footer=last->requested_padding;
                unsigned char *ancestor=*(unsigned char **)(node+0x38);
                while(ancestor){
                    for(BfaRow *parent=owner->rows;parent;parent=parent->next){
                        unsigned char *parent_row=parent->rendering.row;
                        if(*(void **)(parent_row+0x3f0)==ancestor && parent->footer &&
                           parent->footer_last==record->footer_last)footer-=6;
                    }
                    ancestor=*(unsigned char **)(ancestor+0x38);
                }
            }
            int height=bottom-y+footer-(depth>0?2:0);
            if(height<=0)continue;
            BfaPaintFolderBlock(background, canvas, view, x, y, width, height,
                                appearance.color, expanded, row[0x453] || row[0x450]);
        }
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
