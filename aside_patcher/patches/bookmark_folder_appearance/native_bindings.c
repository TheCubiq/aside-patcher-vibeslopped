#include "native_controls.h"
#define NATIVE_CALLS __declspec(guard(nocf))
NATIVE_CALLS void *BfaAllocate(size_t size) {return ((void *(*)(size_t))BfaNativeAddress(0x5df7be0))(size);}
NATIVE_CALLS void BfaFree(void *data,size_t size) {((void (*)(void*,size_t))BfaNativeAddress(0x3ff4ee0))(data,size);}
NATIVE_CALLS const BfaString *BfaGetString(void *prefs,const BfaStringView *key) {return ((const BfaString *(*)(void*,const BfaStringView*))BfaNativeAddress(0x40704d0))(prefs,key);}
NATIVE_CALLS void BfaConstructString(BfaString *out,const char *data,size_t size) {((void (*)(BfaString*,const char*,size_t))BfaNativeAddress(0x3ff5490))(out,data,size);}
NATIVE_CALLS void BfaSetValue(void *prefs,const BfaStringView *key,BfaValue *owned) {((void (*)(void*,const BfaStringView*,BfaValue*))BfaNativeAddress(0x66cb50))(prefs,key,owned);}
NATIVE_CALLS void *native_profile_prefs(uintptr_t owner) {
 void *browser=*(void **)(owner+0x528);
 if(!browser)return 0;
 void *profile=*(void **)((uintptr_t)browser+0x40);
 if(!profile)return 0;
 void *getter=*(void **)(*(uintptr_t *)profile+0x168);
 return ((void *(*)(void*))getter)(profile);
}
NATIVE_CALLS void native_register_preference(void *prefs) {
 void *registry=*(void **)((uintptr_t)prefs+0x28);
 static const char name[]="aside.bookmark_folder_appearance";
 BfaStringView key={name,sizeof(name)-1};
 if(((void *(*)(void*,BfaStringView*))BfaNativeAddress(0x5438c20))(prefs,&key))return;
 BfaValue value={{0}};value.bytes[24]=4;
 ((void (*)(void*,BfaStringView*,BfaValue*,unsigned,unsigned))BfaNativeAddress(0x4caff90))(registry,&key,&value,0,0);
}

void *BfaProfilePrefs(void *owner) {return native_profile_prefs((uintptr_t)owner);}
void BfaRegisterPreference(void *prefs) {native_register_preference(prefs);}
NATIVE_CALLS void *BfaFindFolder(void *owner,int64_t identifier) {return ((void *(*)(void*,int64_t))BfaNativeAddress(0x3884980))(owner,identifier);}

const BfaIcon *BfaIconData(int index) {return BfaNativeIcons+index;}
NATIVE_CALLS void BfaImageModel(void *result, const void *icon, uintptr_t color, int size, void *badge) { ((void (*)(void *result, const void *icon, uintptr_t color, int size, void *badge))BfaNativeAddress(0x912e10))(result,icon,color,size,badge); }
NATIVE_CALLS void BfaSetImageModel(void *view, const void *model) { ((void (*)(void *view, const void *model))BfaNativeAddress(0xace5b0))(view,model); }
NATIVE_CALLS void BfaDestroyImageModel(void *model) { ((void (*)(void *model))BfaNativeAddress(0x4bba970))(model); }
NATIVE_CALLS void BfaRoundedBackground(void **result, uintptr_t color, float radius, int inset) { ((void (*)(void **result, uintptr_t color, float radius, int inset))BfaNativeAddress(0x1db04c0))(result,color,radius,inset); }
NATIVE_CALLS void BfaSetBackground(void *view, void **background) { ((void (*)(void *view, void **background))BfaNativeAddress(0x7d2dd0))(view,background); }
NATIVE_CALLS void BfaSchedulePaint(void *view) { ((void (*)(void *view))BfaNativeAddress(0x562eec0))(view); }
