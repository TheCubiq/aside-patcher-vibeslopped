#pragma once
#include "native_controls.h"
void BfaInstallFolderBackground(BfaOwner *owner, void *view);
int BfaHasFolderBackground(void *view);
int BfaNestedRowInset(void *row);
int BfaPaintInsetRow(void *row,void *canvas,int inset);
void BfaPaintFolderBlocks(BfaOwner *owner, void *canvas, void *view, void *background);
void BfaPaintFolderBlock(void *background, void *canvas, void *view,
                         int x,int y,int width,int height,int color,int expanded,int hovered);
