#pragma once
#include "native_runtime.h"

typedef struct BfaMenu BfaMenu;
typedef struct {
    int32_t references;
    int32_t padding;
    void (*invoke)(void);
    void (*destroy)(void *);
    int (*query)(void *, int);
} BfaBindState;
typedef struct {
    BfaBindState base;
    BfaMenu *menu;
    int action;
    uintptr_t anchor;
} BfaActionState;

void *BfaNativeAddress(size_t rva);
void *BfaNewView(size_t size);
void *BfaBoxView(int horizontal);
void BfaAddChild(void *parent, void *child);
void *BfaColorButton(BfaMenu *menu, int color);
void *BfaActionButton(BfaMenu *menu, const uint16_t *label, int action, int arrow);
size_t BfaLabelSize(const uint16_t *label);
void BfaRetainMenu(BfaMenu *menu);
void BfaEditorAction(BfaActionState *state, void *event);
void BfaEditorHover(BfaActionState *state, void *view);
void BfaDestroyAction(void *state);
int BfaQueryState(void *state, int query);
