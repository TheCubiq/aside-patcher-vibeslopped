#pragma once
#include "native_state.h"

extern const unsigned char BfaCaretIcon[]; /* RVA 0x10c6ab38 */
extern const unsigned char BfaFolderIcon[]; /* RVA 0x10c6a378 */
extern const float BfaRowCornerRadius; /* RVA 0xf99f9d8 */
extern const BfaIcon *BfaIconData(int index);
extern void BfaImageModel(void *result, const void *icon, uintptr_t color, int size, void *badge); /* RVA 0x912e10 */
extern void BfaSetImageModel(void *view, const void *model); /* RVA 0xace5b0 */
extern void BfaDestroyImageModel(void *model); /* RVA 0x4bba970 */
extern void BfaRoundedBackground(void **result, uintptr_t color, float radius, int inset); /* RVA 0x1db04c0 */
extern void BfaSetBackground(void *view, void **owned_background); /* RVA 0x7d2dd0 */
extern void BfaSchedulePaint(void *view); /* RVA 0x562eec0 */

/* Folder identity and native lifetime stay in the runtime registry. */
typedef struct {
    void *row;
} BfaRowRendering;

void BfaRenderRow(BfaRowRendering *rendering, BfaAppearance appearance);
void BfaReleaseRendering(BfaRowRendering *rendering);
