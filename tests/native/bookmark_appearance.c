/* Run with MSVC together with native_state.c and native_model.c. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "native_model.h"

#define UNUSED {"unused",6,0,0,0}
const BfaIcon BfaNativeIcons[48] = {
 {"sun",3,0,0,0},{"moon",4,0,0,0},
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,
 UNUSED,UNUSED,UNUSED,UNUSED,UNUSED,UNUSED
};
const int BfaNativeColors[8]={0,2,8,3,4,1,6,5};
static BfaString stored;
static unsigned writes, allocations;
static const char first[]="f0eca325-e369-4b92-821f-17ed79e60ed0";
static const char nested[]="d7e26231-a052-4488-b4cb-ac9a34f7ff2d";
static const char other[]="a7e26231-a052-4488-b4cb-ac9a34f7ff2d";
void *BfaAllocate(size_t size){void *p=malloc(size);assert(p);++allocations;return p;}
void BfaFree(void *data,size_t size){(void)size;if(data){assert(allocations);--allocations;free(data);}}
void BfaConstructString(BfaString *out,const char *data,size_t size){
 memset(out,0,sizeof(*out));
 if(size<=22){memcpy(out->bytes,data,size);out->bytes[23]=(unsigned char)size;}
 else{char *p=BfaAllocate(size+1);memcpy(p,data,size);p[size]=0;
 memcpy(out->bytes,&p,8);memcpy(out->bytes+8,&size,8);
 size_t capacity=(size+1)|((size_t)1<<63);memcpy(out->bytes+16,&capacity,8);}
}
static void clear_string(BfaString *value){if(value->bytes[23]&128){void *p;memcpy(&p,value->bytes,8);BfaFree(p,0);}memset(value,0,sizeof(*value));}
const BfaString *BfaGetString(void *prefs,const BfaStringView *key){(void)prefs;assert(key->size==32);assert(!memcmp(key->data,"aside.bookmark_folder_appearance",32));return &stored;}
void BfaSetValue(void *prefs,const BfaStringView *key,BfaValue *owned){
 (void)BfaGetString(prefs,key);assert(owned->bytes[24]==4);clear_string(&stored);
 memcpy(stored.bytes,owned->bytes,24);memset(owned,0,sizeof(*owned));++writes;
}
static void set_raw(const char *text){clear_string(&stored);BfaConstructString(&stored,text,strlen(text));}
static void expect(const char *guid,int color,int icon){BfaAppearance value;BfaReadAppearance(0,guid,&value);assert(value.color==color);assert(value.icon==icon);}
static void write(const char *guid,int color,int icon){BfaAppearance value={color,icon};BfaWriteAppearance(0,guid,value);}
static void init_node(unsigned char node[0x140],const char *guid,int folder){
 memset(node,0,0x140);*(int *)(node+0xf8)=folder;BfaConstructString((BfaString *)(node+0x68),guid,36);
}
int main(void){
 expect(first,-1,-1);
 write(first,2,1);expect(first,2,1);
 write(nested,4,0);expect(nested,4,0);expect(first,2,1);
 write(first,8,1);expect(first,8,1);expect(nested,4,0);
 write(first,-1,1);expect(first,-1,1);
 write(first,6,-1);expect(first,6,-1);
 write(first,-1,-1);expect(first,-1,-1);expect(nested,4,0);
 write(first,7,99);expect(first,-1,-1);
 assert(!BfaValidGuid("F0eca325-e369-4b92-821f-17ed79e60ed0",36));
 unsigned before=writes;write("invalid-guid-that-is-long-enough!!!!!",2,0);assert(writes==before);
 set_raw("v1\nf0eca325-e369-4b92-821f-17ed79e60ed0\t7\tmoon\n");expect(first,-1,1);
 set_raw("v1\nf0eca325-e369-4b92-821f-17ed79e60ed0\t4\tunknown\n");expect(first,4,-1);
 set_raw("v1\nf0eca325-e369-4b92-821f-17ed79e60ed0\t2\tsun\nf0eca325-e369-4b92-821f-17ed79e60ed0\t6\tmoon");expect(first,6,1);
 write(first,4,0);expect(first,4,0);BfaStringView bytes=BfaStringBytes(&stored);assert(bytes.size==46);
 set_raw("v2\nf0eca325-e369-4b92-821f-17ed79e60ed0\t4\tsun\n");expect(first,-1,-1);
 set_raw("v1\nmalformed\nf0eca325-e369-4b92-821f-17ed79e60ed0\t\tmoon\n");expect(first,-1,1);
 write(nested,4,0);write(other,1,1);
 unsigned char root[0x140],child[0x140],url[0x140];
 init_node(root,first,1);init_node(child,nested,1);init_node(url,other,0);
 unsigned char *children[]={child,url};*(void **)(root+0x40)=children;*(size_t *)(root+0x48)=2;
 *(void **)(child+0x38)=root;*(void **)(url+0x38)=root;
 before=writes;BfaRemoveSubtreeAppearance(0,root);assert(writes==before+1);
 expect(first,-1,-1);expect(nested,-1,-1);expect(other,1,1);
 before=writes;BfaRemoveSubtreeAppearance(0,root);assert(writes==before);
 clear_string((BfaString *)(root+0x68));clear_string((BfaString *)(child+0x68));clear_string((BfaString *)(url+0x68));clear_string(&stored);
 assert(allocations==0);puts("bookmark appearance storage and subtree cleanup passed");return 0;
}


