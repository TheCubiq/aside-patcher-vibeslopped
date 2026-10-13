#include "native_render.h"

#if defined(_MSC_VER)
#define NATIVE_CALLS __declspec(guard(nocf))
#else
#define NATIVE_CALLS
#endif

/* Native Aside color IDs, using the palette's actual enum order. */
static uintptr_t icon_color(int color) {
    switch (color) {
    case 0: return 0x12d;
    case 2: return 0x118;
    case 8: return 0x11b;
    case 3: return 0x11e;
    case 4: return 0x121;
    case 1: return 0x124;
    case 6: return 0x127;
    case 5: return 0x12a;
    default: return 0;
    }
}

static uintptr_t title_color(int color) {
    uintptr_t result = icon_color(color);
    return color == 0 ? result : result + 1;
}

static void image(void *view, const void *icon, uintptr_t color) {
    unsigned char model[64] = {0};
    BfaImageModel(model, icon, color, 16, 0);
    BfaSetImageModel(view, model);
    BfaDestroyImageModel(model);
}

NATIVE_CALLS void BfaRenderRow(BfaRowRendering *rendering, BfaAppearance appearance) {
    unsigned char *row = rendering->row;
    unsigned char *node = *(unsigned char **)(row + 0x3f0);
    if (!node || *(int *)(node + 0xf8) == 0) return;
    if (!BfaValidColor(appearance.color)) appearance.color = -1;
    unsigned selected = row[0x450];
    uintptr_t color = appearance.color == -1 ? 0xde - selected : icon_color(appearance.color);
    const void *glyph = BfaFolderIcon;
    if (appearance.icon >= 0 && appearance.icon < 48) glyph = BfaIconData(appearance.icon)->vector_icon;
    void *leading = *(void **)(row + 0x430);
    if (leading) image(leading, glyph, color);
    void *title = *(void **)(row + 0x438);
    if (title) {
        uintptr_t value = appearance.color == -1 ? 0xe1 + selected * 3 : title_color(appearance.color);
        void *method = *(void **)(*(uintptr_t *)title + 0x348);
        ((void (*)(void *, uintptr_t))method)(title, value);
    }
    void *caret = *(void **)(row + 0x440);
    if (caret) image(caret, BfaCaretIcon, color);
    BfaSchedulePaint(row);
}

void BfaReleaseRendering(BfaRowRendering *rendering) {
    rendering->row = 0;
}
