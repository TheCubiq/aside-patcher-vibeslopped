/* Aside 1.0.928.2 ABI. This module is not an installable recipe yet. */
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct { const char *data; size_t size; } BfaStringView;
typedef struct { unsigned char bytes[24]; } BfaString;
typedef struct { unsigned char bytes[32]; } BfaValue;
typedef struct { int color; int icon; } BfaAppearance;
typedef struct {
    const char *key;
    size_t key_size;
    void *vector_icon;
    const uint16_t *label;
    size_t label_size;
} BfaIcon;

/* Native bindings are resolved against a hash-verified image by the renderer. */
extern const BfaIcon BfaNativeIcons[48]; /* RVA 0x10e552e0 */
extern const int BfaNativeColors[8];    /* RVA 0x10e4e720 */
extern void *BfaAllocate(size_t size); /* RVA 0x5df7be0 */
extern void BfaFree(void *data, size_t size); /* RVA 0x3ff4ee0 */
extern const BfaString *BfaGetString(void *prefs, const BfaStringView *key); /* RVA 0x40704d0 */
extern void BfaConstructString(BfaString *result, const char *data, size_t size); /* RVA 0x3ff5490 */
extern void BfaSetValue(void *prefs, const BfaStringView *key, BfaValue *owned_value); /* RVA 0x66cb50 */

BfaStringView BfaStringBytes(const BfaString *value);
int BfaValidGuid(const char *guid, size_t size);
int BfaFindIcon(const char *key, size_t size);
int BfaValidColor(int color);
void BfaReadAppearance(void *prefs, const char guid[36], BfaAppearance *result);
void BfaWriteAppearance(void *prefs, const char guid[36], BfaAppearance appearance);
/* Packed 36-byte GUIDs. Remove a deleted subtree in one preference update. */
void BfaRemoveAppearances(void *prefs, const char *guids, size_t count);
