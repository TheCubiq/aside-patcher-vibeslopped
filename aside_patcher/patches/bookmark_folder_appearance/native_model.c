#include "native_model.h"

/* Verified Aside 1.0.928.2 BookmarkNode fields, independent of row lifetime. */
static const unsigned char *next_node(const unsigned char *node,
                                      const unsigned char *root) {
    size_t count = *(const size_t *)(node + 0x48);
    const unsigned char *const *children = *(const unsigned char *const *const *)(node + 0x40);
    if (count) return children[0];
    while (node != root) {
        const unsigned char *parent = *(const unsigned char *const *)(node + 0x38);
        children = *(const unsigned char *const *const *)(parent + 0x40);
        count = *(const size_t *)(parent + 0x48);
        for (size_t i = 0; i < count; ++i) {
            if (children[i] == node) {
                if (i + 1 < count) return children[i + 1];
                break;
            }
        }
        node = parent;
    }
    return 0;
}

void BfaRemoveSubtreeAppearance(void *prefs, const void *root) {
    if (!root) return;
    size_t count = 0;
    const unsigned char *node = root;
    do {
        if (*(const int *)(node + 0xf8) != 0) ++count;
        node = next_node(node, root);
    } while (node);
    if (!count || count > SIZE_MAX / 36) return;
    char *guids = BfaAllocate(count * 36);
    size_t written = 0;
    node = root;
    do {
        if (*(const int *)(node + 0xf8) != 0) {
            BfaStringView guid = BfaStringBytes((const BfaString *)(node + 0x68));
            if (BfaValidGuid(guid.data, guid.size)) {
                for (size_t i = 0; i < 36; ++i) guids[written * 36 + i] = guid.data[i];
                ++written;
            }
        }
        node = next_node(node, root);
    } while (node);
    BfaRemoveAppearances(prefs, guids, written);
    BfaFree(guids, count * 36);
}
