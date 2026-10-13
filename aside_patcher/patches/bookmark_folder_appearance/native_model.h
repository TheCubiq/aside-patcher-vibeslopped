#pragma once
#include "native_state.h"

/* Called before BookmarkModel::Remove while the entire subtree is alive. */
void BfaRemoveSubtreeAppearance(void *prefs, const void *root);
