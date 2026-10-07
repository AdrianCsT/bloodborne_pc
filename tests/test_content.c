#include "runtime.h"
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef int32_t (ABI *Module)(uint16_t);
typedef int32_t (ABI *Init)(const void *,void *);
typedef int32_t (ABI *Param)(uint32_t,int32_t *);
typedef int32_t (ABI *List)(uint32_t,void *,uint32_t,uint32_t *);
#define GET(t,n) ((t)runtime_content_resolve(n))
int main(int argc,char **argv) {
    Module load=GET(Module,"g8cM39EUZ6o#M#N");
    Module loaded=GET(Module,"fMP5NHUOaMk#M#N");
    Module unload=GET(Module,"eR2bZFAAU0Q#M#N");
    Init init=GET(Init,"R9lA82OraNs#c#d");
    Param param=GET(Param,"99b82IKXpH4#c#d");
    List list=GET(List,"xnd8BJzAxmk#c#d");
    assert(load && loaded && unload && init && param && list);
    assert(!runtime_content_resolve("g8cM39EUZ6o#I#J"));
    if (argc>1 && !strcmp(argv[1],"--missing")) { load(0xb4); return 99; }
    /* The other modes expect no add-ons unless they set BB_ADDCONT themselves. */
#ifdef _WIN32
    _putenv_s("BB_ADDCONT","");
#else
    unsetenv("BB_ADDCONT");
#endif
    const uint32_t profile[]={1,13,0x80000000,0,7};
    runtime_content_configure(profile);
    if (argc>1 && !strcmp(argv[1],"--unknown")) {
        /* Other system modules are host-provided: reference counted, independent. */
        assert((uint32_t)loaded(0xb5)==0x805a1001);
        assert(load(0xb5)==0 && loaded(0xb5)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
        assert(unload(0xb5)==0 && (uint32_t)loaded(0xb5)==0x805a1001);
        puts("PASS: independent module references"); return 0;
    }
    if (argc>1 && !strcmp(argv[1],"--addcont")) {
        /* BB_ADDCONT labels come back as installed 24-byte entries; the capacity is respected,
         * unused slots stay untouched and a label over 16 characters is skipped. */
#ifdef _WIN32
        _putenv_s("BB_ADDCONT","SPEXPANSIONDLC03,LABEL_LONGER_THAN_16,SECOND");
#else
        setenv("BB_ADDCONT","SPEXPANSIONDLC03,LABEL_LONGER_THAN_16,SECOND",1);
#endif
        unsigned char initial[32]={0}, boot[40];
        assert(load(0xb4)==0 && init(initial,boot)==0);
        struct { uint32_t hits,canary; } h={99,0xabcdef};
        assert(list(0,NULL,0,&h.hits)==0 && h.hits==2 && h.canary==0xabcdef);
        unsigned char entries[72]; memset(entries,0xaa,sizeof(entries));
        uint32_t status;
        assert(list(0,entries,1,&h.hits)==0 && h.hits==1);
        assert(!strcmp((const char *)entries,"SPEXPANSIONDLC03") && !entries[17] && !entries[19]);
        memcpy(&status,entries+20,4); assert(status==4);
        for (unsigned i=24;i<sizeof(entries);++i) assert(entries[i]==0xaa);
        assert(list(0,entries,3,&h.hits)==0 && h.hits==2);
        assert(!strcmp((const char *)entries+24,"SECOND"));
        memcpy(&status,entries+44,4); assert(status==4);
        for (unsigned i=48;i<sizeof(entries);++i) assert(entries[i]==0xaa);
        puts("PASS: add-on licenses from BB_ADDCONT"); return 0;
    }
    assert((uint32_t)loaded(0)==0x805a1000);
    assert((uint32_t)loaded(0xb4)==0x805a1001);
    assert((uint32_t)unload(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && load(0xb4)==0 && loaded(0xb4)==0);
    unsigned char initial[32]={0};
    struct { unsigned char boot[40]; uint32_t canary; } b;
    memset(&b,0xa5,sizeof(b));
    assert((uint32_t)init(NULL,b.boot)==0x80d90002);
    initial[31]=1; assert((uint32_t)init(initial,b.boot)==0x80d90002); initial[31]=0;
    assert(b.boot[0]==0xa5 && init(initial,b.boot)==0);
    for (unsigned i=0;i<40;++i) assert(b.boot[i]==0);
    assert(b.canary==0xa5a5a5a5);
    b.boot[0]=0xcc; assert((uint32_t)init(initial,b.boot)==0x80d90003 && b.boot[0]==0xcc);
    struct { int32_t value; uint32_t canary; } result={-1,0x1234};
    for (unsigned i=0;i<5;++i) {
        assert(param(i,&result.value)==0 && (uint32_t)result.value==profile[i]);
        assert(result.canary==0x1234);
    }
    result.value=77;
    assert((uint32_t)param(5,&result.value)==0x80d90002 && result.value==77);
    assert((uint32_t)param(0,NULL)==0x80d90002);
    unsigned char entries[48]; memset(entries,0xaa,sizeof(entries));
    struct { uint32_t hits,canary; } h={99,0xabcdef};
    assert(list(0,NULL,0,&h.hits)==0 && h.hits==0 && h.canary==0xabcdef);
    assert(list(0,entries,2,&h.hits)==0 && h.hits==0);
    for (unsigned i=0;i<sizeof(entries);++i) assert(entries[i]==0xaa);
    assert((uint32_t)list(0,NULL,0,NULL)==0x80d90002);
    assert(unload(0xb4)==0 && loaded(0xb4)==0);
    assert((uint32_t)init(initial,b.boot)==0x80d90003);
    assert(unload(0xb4)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && init(initial,b.boot)==0 && unload(0xb4)==0);
    puts("PASS: AppContent lifecycle, profile and output boundaries");
    return 0;
}
