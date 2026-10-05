/* Offline AppContent provider for the configured base-game profile.
 * Add-on licenses come from BB_ADDCONT (entitlement labels, comma-separated, e.g.
 * SPEXPANSIONDLC03 for The Old Hunters, whose data ships with the game). No package/DLC
 * mounting, downloads or entitlement checks. */
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define INVALID_ID ((int32_t)0x805a1000)
#define NOT_LOADED ((int32_t)0x805a1001)
#define PARAMETER ((int32_t)0x80d90002)
#define BUSY ((int32_t)0x80d90003)
static unsigned refs, initialized, configured;
static uint32_t parameters[5];
static unsigned queries, lists;
/* OrbisAppContentAddcontInfo: entitlement label (16 characters, NUL, padding) and status. */
typedef struct { char label[17]; char padding[3]; uint32_t status; } AddcontInfo;
#define ADDCONT_INSTALLED 4u
#define ADDCONT_MAX 8
static char addcont[ADDCONT_MAX][17];
static unsigned addcont_count;
static void load_addcont(void) {
    addcont_count=0;
    for (const char *p=getenv("BB_ADDCONT"); p && *p && addcont_count<ADDCONT_MAX;) {
        size_t n=strcspn(p,",");
        if (n>16) fprintf(stderr,"WARNING: BB_ADDCONT label longer than 16 characters ignored: %.*s\n",(int)n,p);
        else if (n) { memcpy(addcont[addcont_count],p,n); addcont[addcont_count++][n]=0; }
        p+=n; if (*p==',') ++p;
    }
}
void runtime_content_configure(const uint32_t values[5]) {
    if (refs || initialized || (values[0]!=1 && values[0]!=3)) {
        fputs("ERROR: invalid or late content profile\n",stderr); exit(1);
    }
    memcpy(parameters,values,sizeof(parameters)); configured=1;
}
static void require_provider(void) {
    if (!configured) { fputs("STOP: AppContent needs --content-profile\n",stderr); exit(21); }
}
/* System modules: every library this eboot imports is provided by the host
 * runtime, so loading one only tracks a reference count. AppContent (0xb4)
 * additionally requires the configured content profile. */
static unsigned module_refs[0x10000];
static ABI int32_t module_load(uint16_t id) {
    if (!id) return INVALID_ID;
    if (id==0xb4) require_provider();
    if (module_refs[id]==UINT32_MAX) { fputs("STOP: module reference overflow\n",stderr); exit(21); }
    ++module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; printf("Runtime: AppContent host provider loaded; references=%u\n",refs); }
    else printf("Runtime: system module 0x%x loaded (host implementation)\n",id);
    return 0;
}
static ABI int32_t module_loaded(uint16_t id) {
    if (!id) return INVALID_ID;
    return module_refs[id] ? 0 : NOT_LOADED;
}
static ABI int32_t module_unload(uint16_t id) {
    int32_t result=module_loaded(id);
    if (result) return result;
    --module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; if (!refs) initialized=0; }
    return 0;
}
static void require_loaded(void) {
    require_provider();
    if (!refs) { fputs("STOP: AppContent called before module load\n",stderr); exit(21); }
}
static ABI int32_t content_init(const unsigned char *init,unsigned char *boot) {
    require_loaded();
    if (!init || !boot) return PARAMETER;
    if (initialized) return BUSY;
    /* Only the reserved-zero init profile is implemented. */
    for (unsigned i=0;i<32;++i) if (init[i]) return PARAMETER;
    memset(boot,0,40); initialized=1;
    load_addcont();
    if (!addcont_count) puts("Runtime: AppContent initialized; offline base-game profile, no add-ons");
    else {
        printf("Runtime: AppContent initialized; offline base-game profile, add-on licenses:");
        for (unsigned i=0;i<addcont_count;++i) printf(" %s",addcont[i]);
        putchar('\n');
    }
    return 0;
}
static void require_initialized(void) {
    require_loaded();
    if (!initialized) { fputs("STOP: AppContent query before initialization\n",stderr); exit(21); }
}
static ABI int32_t param_int(uint32_t id,int32_t *out) {
    require_initialized();
    if (id>4 || !out) return PARAMETER;
    memcpy(out,&parameters[id],4); ++queries;
    printf("Runtime: AppContent parameter %u = %d\n",id,*out);
    return 0;
}
static ABI int32_t addon_list(uint32_t service,void *list,uint32_t capacity,uint32_t *hits) {
    require_initialized();
    if (service) { fputs("STOP: nonzero AppContent service label unsupported\n",stderr); exit(21); }
    if ((!capacity || !list) && !hits) return PARAMETER;
    /* BB_ADDCONT licenses, reported as installed; nothing is mounted. Without a list only the
     * count is returned. Never touch unused list slots. */
    uint32_t n=addcont_count;
    if (capacity && list) {
        if (n>capacity) n=capacity;
        for (uint32_t i=0;i<n;++i) {
            AddcontInfo info; memset(&info,0,sizeof(info));
            memcpy(info.label,addcont[i],sizeof(info.label)); info.status=ADDCONT_INSTALLED;
            memcpy((unsigned char *)list+i*sizeof(info),&info,sizeof(info));
        }
    }
    if (hits) *hits=n;
    if (!lists) printf("Runtime: AppContent add-on list queried; %u reported\n",n);
    ++lists; return 0;
}
uintptr_t runtime_content_resolve(const char *name) {
    if (!strcmp(name,"g8cM39EUZ6o#M#N")) return (uintptr_t)module_load;
    if (!strcmp(name,"fMP5NHUOaMk#M#N")) return (uintptr_t)module_loaded;
    if (!strcmp(name,"eR2bZFAAU0Q#M#N")) return (uintptr_t)module_unload;
    if (!strcmp(name,"R9lA82OraNs#c#d")) return (uintptr_t)content_init;
    if (!strcmp(name,"99b82IKXpH4#c#d")) return (uintptr_t)param_int;
    if (!strcmp(name,"xnd8BJzAxmk#c#d")) return (uintptr_t)addon_list;
    return 0;
}
void runtime_content_report(void) {
    printf("Runtime: AppContent references=%u, initialized=%u, parameter queries=%u, list queries=%u\n",refs,initialized,queries,lists);
}
