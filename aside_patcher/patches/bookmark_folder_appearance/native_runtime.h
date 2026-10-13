#pragma once
#include "native_render.h"
#include "native_model.h"

typedef struct BfaOwner BfaOwner;
extern void *BfaProfilePrefs(void *owner);
extern void BfaRegisterPreference(void *prefs);
extern void *BfaFindFolder(void *owner, int64_t identifier);

/* All calls run on the browser UI thread, just like the native Views callbacks. */
void BfaRowUpdated(void *row);
void BfaRowDestroyed(void *row);
void BfaOwnerDestroyed(void *owner);
void BfaModelRemoving(void *model, const void *node);
BfaOwner *BfaRetainOwner(void *owner);
void BfaReleaseOwner(BfaOwner *owner);
void *BfaOwnerPointer(BfaOwner *owner);
void BfaAddOwnerReference(BfaOwner *owner);
void BfaPrepareFolderPaint(void *view);
int BfaOwnerAppearance(BfaOwner *owner, int64_t identifier,
                       const char guid[36], BfaAppearance *appearance);
int BfaSetOwnerAppearance(BfaOwner *owner, int64_t identifier,
                          const char guid[36], BfaAppearance appearance);
