/* Save files (/savedataN) are written to a temporary copy and swapped in with one replace, so a
 * crash or a failed replace keeps the previous save (upstream #64, Windows port).
 * After configuring the GPU build: ninja -C out/gpu save-commit-test && out/gpu/save-commit-test.exe
 * (that target embeds the UTF-8 manifest, which the non-ASCII case needs). Standalone, from the
 * repository root in an MSYS2 CLANG64 shell (the non-ASCII case is then skipped):
 * clang -std=c11 -O2 -Wall -Wextra -Werror -UNDEBUG -pthread -Isrc \
 *   tests/test_save_commit_windows.c src/compat_win.c -lbcrypt -o out/save-commit-test.exe
 */
#include "../src/runtime_file.c"
#include <assert.h>

static int32_t guest_errno;
int32_t *runtime_errno(void) { return &guest_errno; }
int32_t runtime_guest_errno(int e) { return e; }
void runtime_memory_note_write(uintptr_t address,uint64_t size) { (void)address; (void)size; }
uintptr_t runtime_lookup(const RuntimeExport *table,size_t n,const char *name) {
    for (size_t i=0;i<n;++i) if (!strcmp(table[i].name,name)) return (uintptr_t)table[i].function;
    return 0;
}

enum { RDONLY=0, WRONLY=1, RDWR=2, CREAT=0x200, TRUNC=0x400 };

static wchar_t *wide(const char *utf8) {
    int n=MultiByteToWideChar(CP_UTF8,0,utf8,-1,NULL,0);
    wchar_t *w=malloc((size_t)n*sizeof(wchar_t));
    assert(w && MultiByteToWideChar(CP_UTF8,0,utf8,-1,w,n)==n);
    return w;
}
static void put_file(const char *path,const char *text) {
    wchar_t *w=wide(path);
    HANDLE h=CreateFileW(w,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    assert(h!=INVALID_HANDLE_VALUE);
    DWORD n; assert(WriteFile(h,text,(DWORD)strlen(text),&n,NULL) && n==strlen(text));
    CloseHandle(h); free(w);
}
/* Contents of a host file, or "(missing)". */
static const char *get_file(const char *path) {
    static char buffer[4][256]; static int turn;
    char *out=buffer[turn++&3];
    wchar_t *w=wide(path);
    HANDLE h=CreateFileW(w,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    free(w);
    if (h==INVALID_HANDLE_VALUE) { strcpy(out,"(missing)"); return out; }
    DWORD n=0; assert(ReadFile(h,out,255,&n,NULL));
    out[n]=0; CloseHandle(h);
    return out;
}
static HANDLE hold_open(const char *path,DWORD share) {
    wchar_t *w=wide(path);
    HANDLE h=CreateFileW(w,GENERIC_READ,share,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    free(w); assert(h!=INVALID_HANDLE_VALUE);
    return h;
}
/* Number of ".bbtmp" files in a host directory (recursively not needed here). */
static int count_temps(const char *dir) {
    char pattern[1100]; snprintf(pattern,sizeof(pattern),"%s\\*",dir);
    wchar_t *w=wide(pattern);
    WIN32_FIND_DATAW find;
    HANDLE h=FindFirstFileW(w,&find);
    free(w);
    int count=0;
    if (h==INVALID_HANDLE_VALUE) return 0;
    do {
        size_t n=wcslen(find.cFileName);
        if (n>6 && !wcscmp(find.cFileName+n-6,L".bbtmp")) ++count;
    } while (FindNextFileW(h,&find));
    FindClose(h);
    return count;
}
/* Names the guest sees when it lists a directory, joined by '|'. */
static const char *guest_listing(const char *guest_dir) {
    static char names[1024];
    int fd=(int)runtime_file_open(guest_dir,0,0); assert(fd>=3);
    char buffer[4096]; int64_t base=0;
    int64_t got=runtime_file_getdents(fd,buffer,sizeof(buffer),&base); assert(got>=0);
    names[0]=0;
    for (int64_t at=0; at<got;) {
        uint16_t reclen; memcpy(&reclen,buffer+at+4,2);
        char name[256]; size_t n=(unsigned char)buffer[at+7];
        memcpy(name,buffer+at+8,n); name[n]=0;
        if (names[0]) strcat(names,"|");
        strcat(names,name);
        at+=reclen;
    }
    assert(!runtime_file_close(fd));
    return names;
}
static int has_name(const char *listing,const char *name) {
    size_t n=strlen(name);
    for (const char *p=listing; (p=strstr(p,name)); ++p)
        if ((p==listing || p[-1]=='|') && (p[n]==0 || p[n]=='|')) return 1;
    return 0;
}
static int64_t write_text(int fd,const char *text) { return runtime_file_write(fd,text,strlen(text)); }

static char root[MAX_PATH],hostfile[MAX_PATH+64];
static const char *host(const char *name) { snprintf(hostfile,sizeof(hostfile),"%s/%s",root,name); return hostfile; }

typedef struct { HANDLE holder; DWORD delay_ms; } Release;
static DWORD WINAPI release_later(void *arg) {
    Release *r=arg; Sleep(r->delay_ms); CloseHandle(r->holder); return 0;
}

int main(void) {
    char temp[MAX_PATH];
    DWORD length=GetTempPathA(sizeof(temp),temp);
    assert(length>0 && length<sizeof(temp));
    assert(GetTempFileNameA(temp,"bbs",0,root));
    assert(DeleteFileA(root) && CreateDirectoryA(root,NULL));
    for (char *p=root; *p; ++p) if (*p=='\\') *p='/';
    assert(!runtime_file_mount("/savedata0",root));
    char path[MAX_PATH+64];

    /* 1. A new save appears in one step: nothing at the final name until the close, no leftovers. */
    int fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|CREAT|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW1")==4);
    assert(!strcmp(get_file(host("slot.dat")),"(missing)"));
    GuestStat st; assert(!runtime_file_stat("/savedata0/slot.dat",&st) && st.size==4); /* the game stats what it writes */
    assert(count_temps(root)==1);
    assert(!has_name(guest_listing("/savedata0"),"slot.dat.1.bbtmp") && !strstr(guest_listing("/savedata0"),"bbtmp"));
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("slot.dat")),"NEW1") && !count_temps(root));
    assert(has_name(guest_listing("/savedata0"),"slot.dat"));

    /* 2. Overwrite: the old contents stay until the close replaces them. */
    put_file(host("slot.dat"),"OLDOLDOLD");
    fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW2")==4);
    assert(!strcmp(get_file(host("slot.dat")),"OLDOLDOLD"));
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("slot.dat")),"NEW2") && !count_temps(root));

    /* 3. Read-write open without truncation starts from the old contents; seeks, reads and
     * truncation work on the copy; a read-only open by path sees the copy meanwhile. */
    put_file(host("rw.dat"),"0123456789");
    fd=(int)runtime_file_open("/savedata0/rw.dat",RDWR,0); assert(fd>=3);
    char buffer[32] = {0};
    assert(runtime_file_read(fd,buffer,4)==4 && !memcmp(buffer,"0123",4));
    assert(runtime_file_lseek(fd,3,0)==3);
    assert(write_text(fd,"ab")==2);
    assert(runtime_file_lseek(fd,0,0)==0);
    assert(runtime_file_read(fd,buffer,10)==10 && !memcmp(buffer,"012ab56789",10));
    assert(!strcmp(get_file(host("rw.dat")),"0123456789"));
    int reader=(int)runtime_file_open("/savedata0/rw.dat",RDONLY,0); assert(reader>=3);
    assert(runtime_file_read(reader,buffer,10)==10 && !memcmp(buffer,"012ab56789",10));
    assert(!runtime_file_close(reader));
    assert(runtime_file_stat("/savedata0/rw.dat",&st)==0 && st.size==10);
    assert(do_ftruncate(fd,8)==0);
    assert(runtime_file_stat("/savedata0/rw.dat",&st)==0 && st.size==8);
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("rw.dat")),"012ab567") && !count_temps(root));

    /* 4. Opened for writing but never written: the file is untouched and no copy is left. */
    put_file(host("idle.dat"),"KEEP");
    fd=(int)runtime_file_open("/savedata0/idle.dat",WRONLY,0); assert(fd>=3);
    assert(count_temps(root)==1);
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("idle.dat")),"KEEP") && !count_temps(root));

    /* 5. Copies a crash left behind are removed when the save directory is mounted. */
    assert(CreateDirectoryA(host("sub"),NULL));
    put_file(host("slot.dat.5.bbtmp"),"half"); put_file(host("sub/inner.dat.9.bbtmp"),"half");
    put_file(host("sub/inner.dat"),"GOOD"); put_file(host("notatemp.bbtmp.txt"),"x");
    runtime_file_unmount("/savedata0");
    assert(!runtime_file_mount("/savedata0",root));
    assert(!strcmp(get_file(host("slot.dat.5.bbtmp")),"(missing)") && !strcmp(get_file(host("sub/inner.dat.9.bbtmp")),"(missing)"));
    assert(!strcmp(get_file(host("sub/inner.dat")),"GOOD") && !strcmp(get_file(host("notatemp.bbtmp.txt")),"x"));
    assert(!strcmp(get_file(host("slot.dat")),"NEW2"));

    /* The game mounts the same save again while another handle writes (SaveData mounts often):
     * the copy that is open stays. */
    fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW5")==4 && count_temps(root)==1);
    assert(!runtime_file_mount("/savedata0",root));
    assert(count_temps(root)==1);
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("slot.dat")),"NEW5") && !count_temps(root));
    put_file(host("slot.dat"),"NEW2");

    /* 6. Files outside /savedataN are written in place, as before. */
    runtime_file_mount("/data",root);
    fd=(int)runtime_file_open("/data/plain.dat",WRONLY|CREAT|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"direct")==6 && count_temps(root)==0);
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("plain.dat")),"direct"));
    runtime_file_unmount("/data");

    /* 7. A reader of the old save that is still open (the CRT default is no delete sharing,
     * these opens allow it) does not stop the replace, and keeps reading the old contents. */
    put_file(host("slot.dat"),"OLD7");
    int old_reader=(int)runtime_file_open("/savedata0/slot.dat",RDONLY,0); assert(old_reader>=3);
    fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW7")==4);
    unsigned retries_before=save_retries_total;
    assert(!runtime_file_close(fd));
    assert(!strcmp(get_file(host("slot.dat")),"NEW7") && save_retries_total==retries_before);
    assert(runtime_file_read(old_reader,buffer,4)==4 && !memcmp(buffer,"OLD7",4));
    assert(!runtime_file_close(old_reader));

    /* 8. A replace that keeps failing (an outside program holds the old file without delete
     * sharing): after the retries the old save is untouched, the close reports it, no copy is left. */
    put_file(host("slot.dat"),"OLD8");
    HANDLE outside=hold_open(host("slot.dat"),FILE_SHARE_READ|FILE_SHARE_WRITE);
    fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW8")==4);
    retries_before=save_retries_total;
    ULONGLONG started=GetTickCount64();
    int64_t r=runtime_file_close(fd);
    ULONGLONG waited=GetTickCount64()-started;
    assert(r<0);
    assert(waited>=1800 && waited<4000);
    assert(save_retries_total>retries_before+3);
    CloseHandle(outside);
    assert(!strcmp(get_file(host("slot.dat")),"OLD8") && !count_temps(root));
    assert(runtime_file_stat("/savedata0/slot.dat",&st)==0 && st.size==4); /* no longer mapped to the dropped copy */

    /* 9. The same holder letting go in time: the retry succeeds. */
    put_file(host("slot.dat"),"OLD9");
    outside=hold_open(host("slot.dat"),FILE_SHARE_READ|FILE_SHARE_WRITE);
    fd=(int)runtime_file_open("/savedata0/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
    assert(write_text(fd,"NEW9")==4);
    Release release={outside,400};
    HANDLE thread=CreateThread(NULL,0,release_later,&release,0,NULL); assert(thread);
    retries_before=save_retries_total;
    started=GetTickCount64();
    assert(!runtime_file_close(fd));
    waited=GetTickCount64()-started;
    WaitForSingleObject(thread,INFINITE); CloseHandle(thread);
    assert(save_retries_total>retries_before && waited<1900);
    assert(!strcmp(get_file(host("slot.dat")),"NEW9") && !count_temps(root));

    /* 10. Non-ASCII names (the manifest selects the UTF-8 code page for the narrow calls). */
    if (GetACP()==CP_UTF8) {
        snprintf(path,sizeof(path),"%s/%s",root,"sa\xc3\xb1" "e-\xe6\x97\xa5\xe6\x9c\xac");
        wchar_t *wd=wide(path); assert(CreateDirectoryW(wd,NULL)); free(wd);
        runtime_file_mount("/savedata1",path);
        put_file(host("sa\xc3\xb1" "e-\xe6\x97\xa5\xe6\x9c\xac/slot.dat"),"OLD10");
        fd=(int)runtime_file_open("/savedata1/slot.dat",WRONLY|TRUNC,0); assert(fd>=3);
        assert(write_text(fd,"NEW10")==5);
        assert(!strcmp(get_file(host("sa\xc3\xb1" "e-\xe6\x97\xa5\xe6\x9c\xac/slot.dat")),"OLD10"));
        assert(!runtime_file_close(fd));
        assert(!strcmp(get_file(host("sa\xc3\xb1" "e-\xe6\x97\xa5\xe6\x9c\xac/slot.dat")),"NEW10"));
        assert(!count_temps(path));
        runtime_file_unmount("/savedata1");
        puts("  non-ASCII save directory: PASS");
    } else puts("  non-ASCII save directory: SKIPPED (no UTF-8 manifest in this build; use ninja save-commit-test)");

    runtime_file_unmount("/savedata0");
    puts("Save files: temporary copy and one replace, failed replace keeps the old save, retries, stale copies PASS");
    return 0;
}
