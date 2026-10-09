/* Guest file system: PS4 mount points mapped onto host directories.
 *   /app0, /hostapp  -> game package root (read-only by convention)
 *   /temp0, /download0, /data, and mounts added by SaveData -> user directory
 * Guest descriptors are small integers in our own table; stdio 0-2 pass through.
 * Paths containing ".." components are rejected rather than normalized. */
#define _GNU_SOURCE
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <windows.h>
/* 64-bit sizes and times; directories are never opened as CRT descriptors. */
typedef struct _stat64 HostStat;
#define host_stat(path,s) _stat64(path,s)
#define host_fstat(fd,s) _fstat64(fd,s)
#define host_lseek _lseeki64
#define O_DIRECTORY 0x40000000 /* bbport marker, removed before _open */
#else
typedef struct stat HostStat;
#define host_stat(path,s) stat(path,s)
#define host_fstat(fd,s) fstat(fd,s)
#define host_lseek lseek
#endif
#define ERR(n) ((int32_t)(UINT32_C(0x80020000)|(n)))
#define MAX_FILES 1024
#define MAX_MOUNTS 16

typedef struct { int64_t sec, nsec; } GuestTimespec;
typedef struct {
    uint32_t dev, ino;
    uint16_t mode, nlink;
    uint32_t uid, gid, rdev;
    GuestTimespec atime, mtime, ctime;
    int64_t size, blocks;
    uint32_t blksize, flags, gen;
    int32_t lspare;
    GuestTimespec birthtime;
} GuestStat;
_Static_assert(sizeof(GuestStat)==120,"FreeBSD stat layout");

typedef struct { char *names; size_t count, *offsets; unsigned char *types; } Listing;
/* commit: a save file opened for writing is written to a temporary copy (temp), which replaces
 * the file (commit) in one rename when closed: a crash or a kill mid-save leaves the old file.
 * must_commit: the open created or truncated the file, so the close replaces it even when nothing
 * was written. unlinked: the file was removed (or replaced by another) while open, so the close
 * drops the copy. */
typedef struct { int used, host, dirty, must_commit, unlinked; Listing *dir; size_t position; char path[512]; char *commit, *temp; } File;
typedef struct { char guest[64]; char host[512]; } Mount;
static File files[MAX_FILES];
static Mount mounts[MAX_MOUNTS];
static size_t mount_count, opens, reads, writes, missing;
static uint64_t bytes_read;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;

static int save_path(const char *p) { return p && !strncmp(p,"/savedata",9); }
static int temp_name(const char *name) {
    size_t n=strlen(name);
    return n>6 && !strcmp(name+n-6,".bbtmp");
}
#ifdef _WIN32
#define SAVE_BINARY O_BINARY
/* Save files go through the wide API on Windows whatever the process code page is (the manifest
 * makes it UTF-8 for the game; this does not rely on it). Their handles allow delete sharing: an
 * open reader of the old save never blocks the replace. Paths use '/' (the API accepts it). */
static int wide_path(const char *utf8,wchar_t *out,size_t count) {
    return MultiByteToWideChar(CP_UTF8,0,utf8,-1,out,(int)count)>0;
}
static int save_open(const char *path,int flags,int mode) {
    (void)mode;
    wchar_t w[2048];
    if (!wide_path(path,w,sizeof(w)/sizeof(*w))) { errno=ENAMETOOLONG; return -1; }
    DWORD access=(flags&3)==O_RDONLY ? GENERIC_READ : (flags&3)==O_WRONLY ? GENERIC_WRITE : GENERIC_READ|GENERIC_WRITE;
    DWORD disposition=(flags&O_CREAT) ? (flags&O_EXCL) ? CREATE_NEW : (flags&O_TRUNC) ? CREATE_ALWAYS : OPEN_ALWAYS
                                      : (flags&O_TRUNC) ? TRUNCATE_EXISTING : OPEN_EXISTING;
    HANDLE h=CreateFileW(w,access,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,disposition,FILE_ATTRIBUTE_NORMAL,NULL);
    if (h==INVALID_HANDLE_VALUE) { errno=compat_errno_from_win32(GetLastError()); return -1; }
    int fd=_open_osfhandle((intptr_t)h,flags&(_O_APPEND|_O_BINARY|_O_NOINHERIT));
    if (fd<0) { CloseHandle(h); errno=EMFILE; }
    return fd;
}
static int save_stat(const char *path,HostStat *s) {
    wchar_t w[2048];
    if (!wide_path(path,w,sizeof(w)/sizeof(*w))) { errno=ENAMETOOLONG; return -1; }
    return _wstat64(w,s);
}
static int save_unlink(const char *path) {
    wchar_t w[2048];
    if (!wide_path(path,w,sizeof(w)/sizeof(*w))) { errno=ENAMETOOLONG; return -1; }
    if (DeleteFileW(w)) return 0;
    errno=compat_errno_from_win32(GetLastError());
    return -1;
}
static int save_same_path(const char *a,const char *b) { return !_stricmp(a,b); }
/* Windows 10 1607+: a rename with POSIX semantics replaces the target even while other handles
 * to it are open, as long as they allow delete sharing. Returns a Windows error code. */
static DWORD rename_posix(const wchar_t *from,const wchar_t *to) {
    typedef struct { DWORD flags; HANDLE root; DWORD length; WCHAR name[1]; } RenameInfoEx;
    HANDLE h=CreateFileW(from,DELETE|SYNCHRONIZE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if (h==INVALID_HANDLE_VALUE) return GetLastError();
    wchar_t full[2048];
    DWORD n=GetFullPathNameW(to,sizeof(full)/sizeof(*full),full,NULL);
    DWORD error=ERROR_INVALID_NAME;
    if (n>0 && n<sizeof(full)/sizeof(*full)) {
        size_t bytes=sizeof(RenameInfoEx)+n*sizeof(WCHAR);
        RenameInfoEx *info=calloc(1,bytes);
        if (info) {
            info->flags=0x1|0x2; /* FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS */
            info->length=n*sizeof(WCHAR);
            memcpy(info->name,full,n*sizeof(WCHAR));
            error=SetFileInformationByHandle(h,(FILE_INFO_BY_HANDLE_CLASS)22 /* FileRenameInfoEx */,info,(DWORD)bytes) ? 0 : GetLastError();
            free(info);
        } else error=ERROR_NOT_ENOUGH_MEMORY;
    }
    CloseHandle(h);
    return error;
}
/* How long a replace keeps trying while another program (an antivirus scan, the search indexer,
 * a backup tool) holds the target. */
#define SAVE_REPLACE_BUDGET_MS 2000
static unsigned save_retries_total;
/* Replaces `to` with `from` in one step; returns 0 or a host errno, and describes a failure in
 * detail (Windows error, tries, time). */
static int save_replace(const char *from,const char *to,char *detail,size_t detail_size) {
    wchar_t a[2048],b[2048];
    if (detail_size) detail[0]=0;
    if (!wide_path(from,a,sizeof(a)/sizeof(*a)) || !wide_path(to,b,sizeof(b)/sizeof(*b))) return ENAMETOOLONG;
    ULONGLONG started=GetTickCount64(), elapsed=0;
    unsigned tries=0;
    DWORD wait=10,error=0;
    for (;;) {
        if (MoveFileExW(a,b,MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) { error=0; break; }
        error=GetLastError();
        if (error!=ERROR_ACCESS_DENIED && error!=ERROR_SHARING_VIOLATION && error!=ERROR_LOCK_VIOLATION) break;
        if (!rename_posix(a,b)) { error=0; break; } /* holders that allow delete sharing */
        elapsed=GetTickCount64()-started;
        if (elapsed>=SAVE_REPLACE_BUDGET_MS) break;
        ++tries; __atomic_add_fetch(&save_retries_total,1,__ATOMIC_RELAXED);
        Sleep(wait);
        wait=wait*2>250 ? 250 : wait*2;
    }
    elapsed=GetTickCount64()-started;
    if (error) {
        snprintf(detail,detail_size,"Windows error %lu, %u retries over %llu ms",(unsigned long)error,tries,(unsigned long long)elapsed);
        return compat_errno_from_win32(error);
    }
    if (tries) printf("Runtime: save file %s replaced after %u retries (%llu ms; another program had it open)\n",to,tries,(unsigned long long)elapsed);
    return 0;
}
/* Copies left by a crash (none of them open): removed when their directory is mounted. */
static void remove_stale_temps(const char *dir,int depth) {
    char pattern[1100];
    wchar_t w[2048];
    if ((size_t)snprintf(pattern,sizeof(pattern),"%s/*",dir)>=sizeof(pattern) || !wide_path(pattern,w,sizeof(w)/sizeof(*w))) return;
    WIN32_FIND_DATAW find;
    HANDLE h=FindFirstFileW(w,&find);
    if (h==INVALID_HANDLE_VALUE) return;
    do {
        if (find.cFileName[0]==L'.') continue;
        char name[1024],path[1100];
        if (!WideCharToMultiByte(CP_UTF8,0,find.cFileName,-1,name,sizeof(name),NULL,NULL)) continue;
        if ((size_t)snprintf(path,sizeof(path),"%s/%s",dir,name)>=sizeof(path)) continue;
        if (find.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY) {
            if (depth<4 && !(find.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)) remove_stale_temps(path,depth+1);
        } else if (temp_name(name)) {
            int open_now=0;
            for (int i=3;i<MAX_FILES;++i) if (files[i].used && files[i].temp && !strcmp(files[i].temp,path)) open_now=1;
            if (!open_now && !save_unlink(path)) printf("Runtime: removed %s (a save interrupted earlier; the file it would have replaced is intact)\n",path);
        }
    } while (FindNextFileW(h,&find));
    FindClose(h);
}
#else
#define SAVE_BINARY 0
#define save_open(path,flags,mode) open(path,flags,mode)
#define save_stat(path,s) stat(path,s)
#define save_unlink(path) unlink(path)
static int save_same_path(const char *a,const char *b) { return !strcmp(a,b); }
static int save_replace(const char *from,const char *to,char *detail,size_t detail_size) {
    if (detail_size) detail[0]=0;
    return rename(from,to) ? errno : 0;
}
static void remove_stale_temps(const char *dir,int depth) {
    DIR *d=opendir(dir);
    if (!d) return;
    for (struct dirent *e; (e=readdir(d));) {
        if (e->d_name[0]=='.') continue;
        char path[1024];
        if ((size_t)snprintf(path,sizeof(path),"%s/%s",dir,e->d_name)>=sizeof(path)) continue;
        if (temp_name(e->d_name)) {
            int open_now=0;
            for (int i=3;i<MAX_FILES;++i) if (files[i].used && files[i].temp && !strcmp(files[i].temp,path)) open_now=1;
            if (!open_now && !unlink(path)) printf("Runtime: removed %s (a save interrupted earlier; the file it would have replaced is intact)\n",path);
        } else if (depth<4 && e->d_type==DT_DIR) remove_stale_temps(path,depth+1);
    }
    closedir(d);
}
#endif

int runtime_file_mount(const char *guest,const char *host) {
    pthread_mutex_lock(&lock);
    for (size_t i=0;i<mount_count;++i) if (!strcmp(mounts[i].guest,guest)) {
        snprintf(mounts[i].host,sizeof(mounts[i].host),"%s",host);
        if (save_path(guest)) remove_stale_temps(mounts[i].host,0);
        pthread_mutex_unlock(&lock); return 0;
    }
    if (mount_count==MAX_MOUNTS || strlen(guest)>=64 || strlen(host)>=512) { pthread_mutex_unlock(&lock); return -1; }
    snprintf(mounts[mount_count].guest,64,"%s",guest);
    snprintf(mounts[mount_count].host,512,"%s",host);
    ++mount_count;
    if (save_path(guest)) remove_stale_temps(host,0);
    pthread_mutex_unlock(&lock);
    return 0;
}
void runtime_file_unmount(const char *guest) {
    pthread_mutex_lock(&lock);
    for (size_t i=0;i<mount_count;++i) if (!strcmp(mounts[i].guest,guest)) {
        mounts[i]=mounts[--mount_count]; break;
    }
    pthread_mutex_unlock(&lock);
}
static char user_root[512]="user";
const char *runtime_file_user_dir(void) { return user_root; }
void runtime_file_configure(const char *app0,const char *user) {
    char path[600];
    snprintf(user_root,sizeof(user_root),"%s",user);
    runtime_file_mount("/app0",app0);
    runtime_file_mount("/hostapp",app0);
    const char *writable[]={"temp0","download0","data"};
    mkdir(user,0755);
    for (int i=0;i<3;++i) {
        snprintf(path,sizeof(path),"%s/%s",user,writable[i]);
        mkdir(path,0755);
        char guest[32]; snprintf(guest,sizeof(guest),"/%s",writable[i]);
        runtime_file_mount(guest,path);
    }
}
/* Resolve a guest path to a host path; returns 0 or a host errno. */
static int translate(const char *guest,char *out,size_t size) {
    if (!guest || !*guest) return ENOENT;
    char buffer[1024];
    if (guest[0]!='/') snprintf(buffer,sizeof(buffer),"/app0/%s",guest);
    else snprintf(buffer,sizeof(buffer),"%s",guest);
    for (const char *p=buffer;(p=strstr(p,".."));p+=2)
        if ((p==buffer || p[-1]=='/') && (p[2]==0 || p[2]=='/')) return EACCES;
    pthread_mutex_lock(&lock);
    size_t best=0; const Mount *m=NULL;
    for (size_t i=0;i<mount_count;++i) {
        size_t n=strlen(mounts[i].guest);
        if (!strncmp(buffer,mounts[i].guest,n) && (buffer[n]=='/' || !buffer[n]) && n>best) { best=n; m=&mounts[i]; }
    }
    int result=0;
    if (!m) result=ENOENT;
    else if ((size_t)snprintf(out,size,"%s%s",m->host,buffer+best)>=size) result=ENAMETOOLONG;
    pthread_mutex_unlock(&lock);
    if (result==ENOENT) fprintf(stderr,"Runtime: no mount for guest path %s\n",guest);
    return result;
}
static int host_flags(int flags) {
    int r;
    switch (flags&3) { case 0: r=O_RDONLY; break; case 1: r=O_WRONLY; break; default: r=O_RDWR; }
    if (flags&0x4) r|=O_NONBLOCK;
    if (flags&0x8) r|=O_APPEND;
    if (flags&0x80) r|=O_SYNC;
    if (flags&0x200) r|=O_CREAT;
    if (flags&0x400) r|=O_TRUNC;
    if (flags&0x800) r|=O_EXCL;
    if (flags&0x20000) r|=O_DIRECTORY;
#ifdef _WIN32
    r|=O_BINARY;
#endif
    return r|O_CLOEXEC;
}
static void free_listing(Listing *l) { if (l) { free(l->names); free(l->offsets); free(l->types); free(l); } }
static Listing *list_directory(const char *path) {
    DIR *d=opendir(path);
    if (!d) return NULL;
    Listing *l=calloc(1,sizeof(*l));
    size_t capacity=0,bytes=0,cap_names=0;
    struct dirent *e;
    while (l && (e=readdir(d))) {
        if (temp_name(e->d_name)) continue;
        size_t n=strlen(e->d_name)+1;
        if (l->count==capacity) {
            capacity=capacity ? capacity*2 : 64;
            l->offsets=realloc(l->offsets,capacity*sizeof(size_t));
            l->types=realloc(l->types,capacity);
        }
        if (bytes+n>cap_names) { cap_names=(bytes+n)*2; l->names=realloc(l->names,cap_names); }
        if (!l->offsets || !l->types || !l->names) { fputs("Out of memory listing directory\n",stderr); exit(1); }
        memcpy(l->names+bytes,e->d_name,n);
        l->offsets[l->count]=bytes;
#ifdef _WIN32
        /* MinGW's dirent has no d_type. */
        char child[1100]; HostStat entry;
        snprintf(child,sizeof(child),"%s/%s",path,e->d_name);
        l->types[l->count]=host_stat(child,&entry) ? 0 : S_ISDIR(entry.st_mode) ? 4 : S_ISREG(entry.st_mode) ? 8 : 0;
#else
        unsigned char type=e->d_type;
        if (type==DT_LNK || type==DT_UNKNOWN) {
            struct stat entry;
            if (!fstatat(dirfd(d),e->d_name,&entry,0))
                type=S_ISDIR(entry.st_mode) ? DT_DIR : S_ISREG(entry.st_mode) ? DT_REG : type;
        }
        l->types[l->count]=type==DT_DIR ? 4 : type==DT_REG ? 8 : type==DT_LNK ? 10 : 0;
#endif
        ++l->count; bytes+=n;
    }
    closedir(d);
    return l;
}
static void convert_stat(const HostStat *s,GuestStat *g) {
    memset(g,0,sizeof(*g));
    g->dev=(uint32_t)s->st_dev; g->ino=(uint32_t)s->st_ino;
    g->nlink=(uint16_t)s->st_nlink;
    g->size=s->st_size;
#ifdef _WIN32
    /* FreeBSD mode bits: directories rwxr-xr-x, files rw-r--r--. */
    g->mode=(uint16_t)(S_ISDIR(s->st_mode) ? 0040755 : 0100644);
    g->blocks=(s->st_size+511)/512; g->blksize=4096;
    g->atime=(GuestTimespec){s->st_atime,0};
    g->mtime=(GuestTimespec){s->st_mtime,0};
    g->ctime=(GuestTimespec){s->st_ctime,0};
#else
    g->mode=(uint16_t)s->st_mode;
    g->blocks=s->st_blocks; g->blksize=(uint32_t)s->st_blksize;
    g->atime=(GuestTimespec){s->st_atim.tv_sec,s->st_atim.tv_nsec};
    g->mtime=(GuestTimespec){s->st_mtim.tv_sec,s->st_mtim.tv_nsec};
    g->ctime=(GuestTimespec){s->st_ctim.tv_sec,s->st_ctim.tv_nsec};
#endif
    g->birthtime=g->ctime;
}
static File *get(int fd) {
    if (fd<3 || fd>=MAX_FILES || !files[fd].used) return NULL;
    return &files[fd];
}
/* BB_AUDIO_TRACE=1: sound file opens and failed reads (missing game sounds). */
static int audio_trace(void) { static int v=-1; if (v<0) { const char *e=getenv("BB_AUDIO_TRACE"); v=e && e[0]=='1'; } return v; }
/* BB_SAVE_TRACE=1: every operation on save files (/savedataN). */
static int save_trace(void) { static int v=-1; if (v<0) { const char *e=getenv("BB_SAVE_TRACE"); v=e && e[0]=='1'; } return v; }
/* Game mounts (including linked mod overlays) are read-only. Saves use other mounts. */
static int game_path(const char *p) {
    if (!p || !*p) return 0;
    if (*p!='/') return 1;
    return (!strncmp(p,"/app0",5) && (!p[5] || p[5]=='/')) ||
           (!strncmp(p,"/hostapp",8) && (!p[8] || p[8]=='/'));
}
/* Saves (/savedataN): see File. The copy starts as the old file unless the open truncates. */
static unsigned temp_serial;
static int copy_contents(int from,int to) {
    char buffer[65536];
    for (;;) {
        ssize_t n=read(from,buffer,sizeof(buffer));
        if (n<0 && errno==EINTR) continue;
        if (n<=0) return n<0 ? -1 : 0;
        for (ssize_t done=0; done<n;) {
            ssize_t w=write(to,buffer+done,(size_t)(n-done));
            if (w<0 && errno==EINTR) continue;
            if (w<0) return -1;
            done+=w;
        }
    }
}
/* A save file open for writing is its copy to the operations by path too (the game stats the
 * file it is writing, which may not exist yet: it renames the old one to its backup first). */
static void current_copy(char *path,size_t size,int write) {
    pthread_mutex_lock(&lock);
    for (int i=3;i<MAX_FILES;++i)
        if (files[i].used && files[i].commit && !files[i].unlinked && save_same_path(files[i].commit,path)) { snprintf(path,size,"%s",files[i].temp); files[i].dirty|=write; break; }
    pthread_mutex_unlock(&lock);
}
/* A rename or unlink by path of a save file a descriptor writes: the written data follows the
 * file, as under POSIX. Moves the pending commit of every writer of `from` to `to` and orphans
 * the writers of the file `to` replaces; with no `to` (unlink) it orphans the writers of `from`.
 * `done`: the host operation succeeded (when it failed because `from` is missing, the writers'
 * copy is the file). Returns the number of writers of `from`. Called with `lock` held, and held
 * since before the host operation: a close on another thread cannot commit to the old name in
 * between. That cannot deadlock: nothing the host operation calls (save_replace, rename, unlink)
 * takes `lock`, and translate(), which does, ran before. The cost is that other opens and closes
 * wait for a replace that retries (up to SAVE_REPLACE_BUDGET_MS on Windows, when another program
 * holds the target), the same wait the renaming thread has. */
static int writers_follow(const char *from,const char *to,int done) {
    int found=0;
    for (int i=3;i<MAX_FILES;++i)
        if (files[i].used && files[i].commit && !files[i].unlinked && save_same_path(files[i].commit,from)) ++found;
    if (done || found)
        for (int i=3;i<MAX_FILES;++i) {
            File *f=&files[i];
            if (!f->used || !f->commit || f->unlinked) continue;
            if (save_same_path(f->commit,from)) {
                char *moved=to ? strdup(to) : NULL;
                if (moved) { free(f->commit); f->commit=moved; }
                else if (!to) f->unlinked=1;
            } else if (to && save_same_path(f->commit,to)) f->unlinked=1;
        }
    return found;
}
/* Returns the descriptor of the copy, or -(errno). */
static int open_for_commit(const char *path,const char *source,int flags,int mode,char **commit,char **temp,int *must_commit) {
    int hf=host_flags(flags);
#ifdef _WIN32
    hf&=~O_DIRECTORY;
#endif
    HostStat s;
    int exists=!save_stat(source,&s);
    if (exists && !S_ISREG(s.st_mode)) { int h=save_open(path,hf,mode ? mode : 0644); return h<0 ? -errno : h; }
    if (exists && (hf&O_CREAT) && (hf&O_EXCL)) return -EEXIST;
    if (!exists && !(hf&O_CREAT)) return -ENOENT;
    size_t n=strlen(path)+32;
    char *t=malloc(n), *c=strdup(path);
    if (!t || !c) { free(t); free(c); return -ENOMEM; }
    snprintf(t,n,"%s.%u.bbtmp",path,__atomic_add_fetch(&temp_serial,1,__ATOMIC_RELAXED));
#ifdef _WIN32
    int perm=0644;
#else
    int perm=exists ? (int)(s.st_mode&07777) : mode ? mode : 0644;
#endif
    int out=save_open(t,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|SAVE_BINARY,perm);
    int e=out<0 ? errno : 0;
    if (!e && exists && !(hf&O_TRUNC)) {
        int in=save_open(source,O_RDONLY|O_CLOEXEC|SAVE_BINARY,0);
        if (in<0 || copy_contents(in,out)) e=errno ? errno : EIO;
        if (in>=0) close(in);
    }
    if (out>=0) close(out);
    int host=e ? -1 : save_open(t,hf&~(O_CREAT|O_EXCL|O_TRUNC),0);
    if (host<0) {
        if (!e) e=errno;
        save_unlink(t); free(t); free(c);
        return -e;
    }
    *commit=c; *temp=t;
    *must_commit=(hf&O_TRUNC) || !exists;
    return host;
}
/* The file a /savedata open really opens (the copy, when a writer has one), plus the copy for a
 * write. Returns the descriptor or -1 with errno. */
static int open_save_file(const char *path,int flags,int mode,char **commit,char **temp,int *must_commit) {
    char current[1024];
    snprintf(current,sizeof(current),"%s",path);
    current_copy(current,sizeof(current),0);
    if (flags&3) {
        int host=open_for_commit(path,current,flags,mode,commit,temp,must_commit);
        if (host<0) { errno=-host; return -1; }
        return host;
    }
    int hf=host_flags(flags);
#ifdef _WIN32
    hf&=~O_DIRECTORY;
#endif
    return save_open(current,hf,mode ? mode : 0644);
}
#ifndef _WIN32
static void sync_directory(const char *file) {
    char dir[1024];
    snprintf(dir,sizeof(dir),"%s",file);
    char *slash=strrchr(dir,'/');
    if (!slash) return;
    *slash=0;
    int d=open(dir,O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if (d>=0) { fsync(d); close(d); }
}
#else
static void sync_directory(const char *file) { (void)file; } /* the replace writes through */
#endif
static int commit_file(const File *f) {
    /* opened for writing but not written (and not created or truncated), or its file is gone */
    if (f->unlinked || (!f->dirty && !f->must_commit)) { close(f->host); save_unlink(f->temp); return 0; }
    int e=fsync(f->host) ? errno : 0;
    close(f->host);
    char detail[128]="";
    if (!e) e=save_replace(f->temp,f->commit,detail,sizeof(detail));
    if (e) {
        fprintf(stderr,"Runtime: save file %s not replaced (%s%s%s); the old one is kept\n",f->path,strerror(e),detail[0] ? ", " : "",detail);
        save_unlink(f->temp);
        return -e;
    }
    sync_directory(f->commit);
    return 0;
}
/* All operations return >=0 or -(host errno); wrappers adapt the convention. */
static int64_t do_open(const char *guest,int flags,int mode) {
    if (game_path(guest) && (flags & (3|0x8|0x200|0x400|0x800))) return -EROFS;
    char path[1024];
    int e=translate(guest,path,sizeof(path));
    if (e) return -e;
#ifdef _WIN32
    /* Windows cannot open a directory as a CRT descriptor: directories are listings only. */
    HostStat s;
    Listing *dir=NULL;
    char *commit=NULL, *temp=NULL;
    int host=-1, must_commit=0;
    int stat_error=host_stat(path,&s) ? errno : 0;
    if (!stat_error && S_ISDIR(s.st_mode)) {
        if (flags&3) return -EISDIR;
        if (!(dir=list_directory(path))) return -EACCES;
    } else if (flags&0x20000) {
        e=stat_error ? stat_error : ENOTDIR;
        if (e==ENOENT) { ++missing; printf("Runtime: open(%s) -> not found\n",guest); }
        return -e;
    } else {
        host=save_path(guest) ? open_save_file(path,flags,mode,&commit,&temp,&must_commit)
                              : open(path,host_flags(flags)&~O_DIRECTORY,_S_IREAD|_S_IWRITE);
        if (save_trace() && save_path(guest))
            printf("Save trace: open(%s, flags 0x%x) -> host %d%s%s\n",guest,flags,host,temp ? ", copy " : "",temp ? temp : "");
        if (host<0) {
            e=errno;
            if (e==ENOENT) { ++missing; printf("Runtime: open(%s) -> not found\n",guest); }
            return -e;
        }
        if (host_fstat(host,&s)) memset(&s,0,sizeof(s));
    }
#else
    char *commit=NULL, *temp=NULL;
    int must_commit=0;
    int host=save_path(guest) ? open_save_file(path,flags,mode,&commit,&temp,&must_commit) : open(path,host_flags(flags),mode ? mode : 0644);
    if (save_trace() && save_path(guest))
        printf("Save trace: open(%s, flags 0x%x) -> host %d%s%s\n",guest,flags,host,temp ? ", copy " : "",temp ? temp : "");
    if (host<0) {
        e=errno;
        if (e==ENOENT) { ++missing; printf("Runtime: open(%s) -> not found\n",guest); }
        return -e;
    }
    struct stat s;
    Listing *dir=NULL;
    if (!fstat(host,&s) && S_ISDIR(s.st_mode)) dir=list_directory(path);
#endif
    pthread_mutex_lock(&lock);
    int fd=-1;
    for (int i=3;i<MAX_FILES;++i) if (!files[i].used) { fd=i; break; }
    if (fd<0) {
        pthread_mutex_unlock(&lock); if (host>=0) close(host); free_listing(dir);
        if (temp) save_unlink(temp);
        free(commit); free(temp); return -EMFILE;
    }
    files[fd]=(File){.used=1,.host=host,.dir=dir,.commit=commit,.temp=temp,.must_commit=must_commit};
    snprintf(files[fd].path,sizeof(files[fd].path),"%s",guest);
    ++opens;
    pthread_mutex_unlock(&lock);
    if (audio_trace() && strstr(guest,"sound/")) printf("Audio trace: open(%s) -> fd %d, %lld bytes\n",guest,fd,(long long)s.st_size);
    const char *mod_trace=getenv("BB_MOD_TRACE"), *mod_root=getenv("BB_MODS_DIR");
    if (mod_trace && mod_trace[0]=='1' && mod_root) {
        char actual[PATH_MAX],root[PATH_MAX];
        static unsigned traced;
        if (realpath(path,actual) && realpath(mod_root,root)) {
            size_t n=strlen(root);
            if (!strncmp(actual,root,n) && (actual[n]=='/' || actual[n]=='\\') &&
                __atomic_fetch_add(&traced,1,__ATOMIC_RELAXED)<32)
                printf("Mods: open %s -> %s\n",guest,actual);
        }
    }
    if (save_trace() && save_path(guest)) printf("Save trace: open(%s) -> fd %d\n",guest,fd);
    return fd;
}
static int64_t do_close(int fd) {
    if (fd>=0 && fd<3) return 0;
    pthread_mutex_lock(&lock);
    File *f=get(fd);
    if (!f) { pthread_mutex_unlock(&lock); return -EBADF; }
    File closed=*f;
    *f=(File){0};
    pthread_mutex_unlock(&lock);
    free_listing(closed.dir);
    int result=0;
    if (closed.temp) result=commit_file(&closed);
    else if (closed.host>=0) close(closed.host);
    if (save_trace() && save_path(closed.path))
        printf("Save trace: close(fd %d, %s)%s -> %d\n",fd,closed.path,closed.temp ? closed.unlinked ? " dropped (file removed)" : closed.dirty || closed.must_commit ? " commit" : " unwritten" : "",result);
    free(closed.commit); free(closed.temp);
    return result;
}
static int host_fd(int fd) {
    if (fd>=0 && fd<3) return fd;
    File *f=get(fd);
    return f ? f->host : -1;
}
static int host_fd_written(int fd) {
    if (fd>=0 && fd<3) return fd;
    File *f=get(fd);
    if (!f) return -1;
    if (!f->dirty && save_trace() && save_path(f->path)) printf("Save trace: first write to fd %d (%s)\n",fd,f->path);
    f->dirty=1;
    return f->host;
}
#ifdef _WIN32
/* The CRT reads and writes at most INT_MAX bytes per call. */
static ssize_t host_read(int h,void *buffer,uint64_t size) { return read(h,buffer,(unsigned)(size>0x7ffff000 ? 0x7ffff000 : size)); }
static ssize_t host_write(int h,const void *buffer,uint64_t size) { return write(h,buffer,(unsigned)(size>0x7ffff000 ? 0x7ffff000 : size)); }
#else
#define host_read read
#define host_write write
#endif
/* The GPU side is told of the write first (runtime_memory_note_write: its tracking unprotects the
 * range). Pages protected again meanwhile would make the kernel's copy fail with EFAULT instead
 * of faulting to our handler: a user-mode write to each page first goes through the handler. */
static void touch_for_write(void *buffer,uint64_t size) {
    if (!size) return;
    uintptr_t p=(uintptr_t)buffer & ~(uintptr_t)4095, end=(uintptr_t)buffer+size;
    for (; p<end; p+=4096) {
        volatile unsigned char *b=(volatile unsigned char *)(p<(uintptr_t)buffer ? (uintptr_t)buffer : p);
        *b=*b;
    }
}
static int64_t do_read(int fd,void *buffer,uint64_t size) {
    int h=host_fd(fd);
    if (h<0) return -EBADF;
    runtime_memory_note_write((uintptr_t)buffer,size);
    touch_for_write(buffer,size);
    ssize_t n=host_read(h,buffer,size);
    if (n<0) { if (audio_trace()) printf("Audio trace: read(fd %d, %llu) failed, errno %d\n",fd,(unsigned long long)size,errno); return -errno; }
    if (n>0) runtime_memory_note_write((uintptr_t)buffer,(uint64_t)n); /* and once the data is there */
    __atomic_add_fetch(&reads,1,__ATOMIC_RELAXED); __atomic_add_fetch(&bytes_read,(uint64_t)n,__ATOMIC_RELAXED);
    return n;
}
static int64_t do_pread(int fd,void *buffer,uint64_t size,int64_t offset) {
    int h=host_fd(fd);
    if (h<0) return -EBADF;
    runtime_memory_note_write((uintptr_t)buffer,size);
    touch_for_write(buffer,size);
    ssize_t n=pread(h,buffer,size,offset);
    if (n<0) { if (audio_trace()) printf("Audio trace: pread(fd %d, %llu @%lld) failed, errno %d\n",fd,(unsigned long long)size,(long long)offset,errno); return -errno; }
    if (n>0) runtime_memory_note_write((uintptr_t)buffer,(uint64_t)n); /* and once the data is there */
    __atomic_add_fetch(&reads,1,__ATOMIC_RELAXED); __atomic_add_fetch(&bytes_read,(uint64_t)n,__ATOMIC_RELAXED);
    return n;
}
static int64_t do_write(int fd,const void *buffer,uint64_t size) {
    int h=host_fd_written(fd);
    if (h<0) return -EBADF;
    ssize_t n=host_write(h,buffer,size);
    if (n<0) return -errno;
    __atomic_add_fetch(&writes,1,__ATOMIC_RELAXED);
    return n;
}
static int64_t do_pwrite(int fd,const void *buffer,uint64_t size,int64_t offset) {
    int h=host_fd_written(fd);
    if (h<0) return -EBADF;
    ssize_t n=pwrite(h,buffer,size,offset);
    return n<0 ? -errno : n;
}
static int64_t do_lseek(int fd,int64_t offset,int whence) {
    if (save_trace()) { File *t=get(fd); if (t && save_path(t->path)) printf("Save trace: lseek(fd %d, %lld, %d)\n",fd,(long long)offset,whence); }
    File *f=get(fd);
    if (!f) return -EBADF;
    if (whence<0 || whence>2) return -EINVAL;
    if (f->dir) {
        /* Directory offsets are entry indices for getdirentries. */
        if (whence==0 && offset>=0) { f->position=(size_t)offset; return offset; }
        return -EINVAL;
    }
    int64_t r=host_lseek(f->host,offset,whence);
    return r<0 ? -errno : r;
}
static int64_t do_fstat(int fd,GuestStat *out) {
    if (save_trace()) { File *t=get(fd); if (t && save_path(t->path)) printf("Save trace: fstat(fd %d)\n",fd); }
    int h=host_fd(fd);
    HostStat s;
#ifdef _WIN32
    if (h<0) { /* directory: a listing only, stat the path it was opened with */
        File *f=get(fd); char path[1024];
        if (!f || !f->dir) return -EBADF;
        if (!out) return -EFAULT;
        if (translate(f->path,path,sizeof(path)) || host_stat(path,&s)) return -EBADF;
        convert_stat(&s,out); return 0;
    }
#else
    if (h<0) return -EBADF;
#endif
    if (!out) return -EFAULT;
    if (host_fstat(h,&s)) return -errno;
    convert_stat(&s,out); return 0;
}
static int64_t do_stat(const char *guest,GuestStat *out) {
    char path[1024]; HostStat s;
    int e=translate(guest,path,sizeof(path));
    if (e) return -e;
    if (save_path(guest)) current_copy(path,sizeof(path),0);
    if (!out) return -EFAULT;
    int r=host_stat(path,&s) ? -errno : 0;
    if (save_trace() && save_path(guest)) printf("Save trace: stat(%s) -> %d, %lld bytes\n",guest,r,r ? -1LL : (long long)s.st_size);
    if (r) return r;
    convert_stat(&s,out); return 0;
}
static int64_t do_getdents(int fd,char *buffer,uint64_t size,int64_t *basep) {
    pthread_mutex_lock(&lock);
    File *f=get(fd);
    int64_t result=0;
    if (!f) result=-EBADF;
    else if (!f->dir) result=-EINVAL;
    else if (!buffer) result=-EFAULT;
    else if (size<512) result=-EINVAL;
    else {
        if (basep) *basep=(int64_t)f->position;
        uint64_t written=0;
        while (f->position<f->dir->count) {
            const char *name=f->dir->names+f->dir->offsets[f->position];
            size_t n=strlen(name); if (n>255) n=255;
            uint16_t reclen=(uint16_t)((8+n+1+7)&~(size_t)7);
            if (written+reclen>size) break;
            char *p=buffer+written;
            memset(p,0,reclen);
            uint32_t ino=(uint32_t)f->position+1;
            memcpy(p,&ino,4); memcpy(p+4,&reclen,2);
            p[6]=(char)f->dir->types[f->position]; p[7]=(char)n;
            memcpy(p+8,name,n);
            written+=reclen; ++f->position;
        }
        result=(int64_t)written;
    }
    pthread_mutex_unlock(&lock);
    return result;
}
static int64_t path_op(const char *guest,int op,int mode) {
    if (game_path(guest)) return -EROFS;
    char path[1024];
    int e=translate(guest,path,sizeof(path));
    if (e) return -e;
    const int save=op==2 && save_path(guest);
    if (save) pthread_mutex_lock(&lock); /* until the writers know (see writers_follow) */
    int r= op==0 ? mkdir(path,mode ? mode : 0755) : op==1 ? rmdir(path) : unlink(path);
    r=r ? -errno : 0;
    if (save) {
        if ((!r || r==-ENOENT) && writers_follow(path,NULL,!r)) r=0; /* the open writer's copy was the file */
        pthread_mutex_unlock(&lock);
    }
    if (save_trace() && save_path(guest)) printf("Save trace: %s(%s) -> %d\n",op==0 ? "mkdir" : op==1 ? "rmdir" : "unlink",guest,r);
    return r;
}
static int64_t do_rename(const char *from,const char *to) {
    if (game_path(from) || game_path(to)) return -EROFS;
    char a[1024],b[1024];
    int e=translate(from,a,sizeof(a));
    if (!e) e=translate(to,b,sizeof(b));
    if (e) return -e;
    int r, save=save_path(from) || save_path(to);
    char detail[128]="";
    if (save) pthread_mutex_lock(&lock); /* until the writers know (see writers_follow) */
#ifdef _WIN32
    /* POSIX rename replaces an existing target; the CRT's fails. Saves go through the replace of
     * their own copies: it retries while another program holds the target. */
    if (save) r=-save_replace(a,b,detail,sizeof(detail));
    else r=MoveFileExA(a,b,MOVEFILE_REPLACE_EXISTING) ? 0 : -compat_errno_from_win32(GetLastError());
#else
    r=rename(a,b) ? -errno : 0;
#endif
    /* Open writers of the file follow it to its new name (their copy was the file, if it is missing). */
    if (save) {
        if ((!r || r==-ENOENT) && !save_same_path(a,b) && writers_follow(a,b,!r)) r=0;
        pthread_mutex_unlock(&lock);
    }
#ifdef _WIN32
    if (save && r) fprintf(stderr,"Runtime: rename(%s, %s) failed (%s%s%s)\n",from,to,strerror(-r),detail[0] ? ", " : "",detail);
#else
    (void)detail;
#endif
    if (save_trace() && (save_path(from) || save_path(to))) printf("Save trace: rename(%s, %s) -> %d\n",from,to,r);
    return r;
}
static int64_t do_ftruncate(int fd,int64_t length) {
    int h=host_fd_written(fd);
    if (h<0) return -EBADF;
#ifdef _WIN32
    return _chsize_s(h,length) ? -errno : 0;
#else
    return ftruncate(h,length) ? -errno : 0;
#endif
}
static int64_t do_truncate(const char *guest,int64_t length) {
    if (game_path(guest)) return -EROFS;
    char path[1024];
    int e=translate(guest,path,sizeof(path));
    if (e) return -e;
    if (save_path(guest)) current_copy(path,sizeof(path),1);
    if (save_trace() && save_path(guest)) printf("Save trace: truncate(%s, %lld)\n",guest,(long long)length);
#ifdef _WIN32
    int h=open(path,O_RDWR|O_BINARY);
    if (h<0) return -errno;
    int r=_chsize_s(h,length) ? -errno : 0;
    close(h);
    return r;
#else
    return truncate(path,length) ? -errno : 0;
#endif
}
static int64_t do_fsync(int fd) { int h=host_fd(fd); if (h<0) return -EBADF; return fsync(h) ? -errno : 0; }
static int64_t do_access(const char *guest,int mode) {
    char path[1024];
    int e=translate(guest,path,sizeof(path));
    if (e) return -e;
    if (save_path(guest)) current_copy(path,sizeof(path),0);
#ifdef _WIN32
    return access(path,mode&6) ? -errno : 0; /* X_OK is invalid for the CRT */
#else
    return access(path,mode&7) ? -errno : 0;
#endif
}

/* Convention adapters: sceKernel* -> Orbis error codes, POSIX -> -1 + errno. */
static int64_t sce(int64_t r) { return r<0 ? ERR(runtime_guest_errno((int)-r)) : r; }
static int64_t posix(int64_t r) { if (r<0) { *runtime_errno()=runtime_guest_errno((int)-r); return -1; } return r; }
#define PAIR(name,params,args) \
    static ABI int64_t sce_##name params { return sce(do_##name args); } \
    static ABI int64_t posix_##name params { return posix(do_##name args); }
PAIR(open,(const char *p,int f,int m),(p,f,m))
PAIR(close,(int fd),(fd))
PAIR(read,(int fd,void *b,uint64_t n),(fd,b,n))
PAIR(pread,(int fd,void *b,uint64_t n,int64_t o),(fd,b,n,o))
PAIR(write,(int fd,const void *b,uint64_t n),(fd,b,n))
PAIR(pwrite,(int fd,const void *b,uint64_t n,int64_t o),(fd,b,n,o))
PAIR(lseek,(int fd,int64_t o,int w),(fd,o,w))
PAIR(fstat,(int fd,GuestStat *s),(fd,s))
PAIR(stat,(const char *p,GuestStat *s),(p,s))
PAIR(getdents,(int fd,char *b,uint64_t n,int64_t *base),(fd,b,n,base))
PAIR(rename,(const char *a,const char *b),(a,b))
PAIR(ftruncate,(int fd,int64_t l),(fd,l))
PAIR(truncate,(const char *p,int64_t l),(p,l))
PAIR(fsync,(int fd),(fd))
static ABI int64_t posix_access(const char *p,int m) { return posix(do_access(p,m)); }
static ABI int64_t sce_mkdir(const char *p,int m) { return sce(path_op(p,0,m)); }
static ABI int64_t posix_mkdir(const char *p,int m) { return posix(path_op(p,0,m)); }
static ABI int64_t sce_rmdir(const char *p) { return sce(path_op(p,1,0)); }
static ABI int64_t posix_rmdir(const char *p) { return posix(path_op(p,1,0)); }
static ABI int64_t sce_unlink(const char *p) { return sce(path_op(p,2,0)); }
static ABI int64_t posix_unlink(const char *p) { return posix(path_op(p,2,0)); }
static ABI int32_t sce_check_reachability(const char *p) {
    GuestStat s; return (int32_t)sce(do_stat(p,&s));
}

static const RuntimeExport exports[]={
    {"sceKernelOpen",sce_open}, {"open",posix_open}, {"_open",posix_open},
    {"sceKernelClose",sce_close}, {"close",posix_close}, {"_close",posix_close},
    {"sceKernelRead",sce_read}, {"read",posix_read}, {"_read",posix_read},
    {"sceKernelPread",sce_pread}, {"pread",posix_pread},
    {"sceKernelWrite",sce_write}, {"write",posix_write}, {"_write",posix_write},
    {"sceKernelPwrite",sce_pwrite}, {"pwrite",posix_pwrite},
    {"sceKernelLseek",sce_lseek}, {"lseek",posix_lseek},
    {"sceKernelFstat",sce_fstat}, {"fstat",posix_fstat},
    {"sceKernelStat",sce_stat}, {"stat",posix_stat},
    {"sceKernelGetdirentries",sce_getdents}, {"getdirentries",posix_getdents},
    {"sceKernelRename",sce_rename}, {"rename",posix_rename},
    {"sceKernelFtruncate",sce_ftruncate}, {"ftruncate",posix_ftruncate},
    {"sceKernelTruncate",sce_truncate}, {"truncate",posix_truncate},
    {"sceKernelFsync",sce_fsync}, {"fsync",posix_fsync},
    {"access",posix_access}, {"sceKernelCheckReachability",sce_check_reachability},
    {"sceKernelMkdir",sce_mkdir}, {"mkdir",posix_mkdir},
    {"sceKernelRmdir",sce_rmdir}, {"rmdir",posix_rmdir},
    {"sceKernelUnlink",sce_unlink}, {"unlink",posix_unlink},
};
uintptr_t runtime_file_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
/* Host-side helpers for other modules (e.g. SaveData, Fios). */
int64_t runtime_file_open(const char *p,int f,int m) { return do_open(p,f,m); }
int64_t runtime_file_close(int fd) { return do_close(fd); }
int64_t runtime_file_read(int fd,void *b,uint64_t n) { return do_read(fd,b,n); }
int64_t runtime_file_pread(int fd,void *b,uint64_t n,int64_t o) { return do_pread(fd,b,n,o); }
int64_t runtime_file_write(int fd,const void *b,uint64_t n) { return do_write(fd,b,n); }
int64_t runtime_file_lseek(int fd,int64_t o,int w) { return do_lseek(fd,o,w); }
int64_t runtime_file_stat(const char *p,void *s) { return do_stat(p,s); }
int64_t runtime_file_fstat(int fd,void *s) { return do_fstat(fd,s); }
int64_t runtime_file_getdents(int fd,char *b,uint64_t n,int64_t *base) { return do_getdents(fd,b,n,base); }
int runtime_file_translate(const char *guest,char *out,size_t size) { return translate(guest,out,size); }
/* Host files replaced in one step (SaveData's param.bin and icon): 0 or a host errno. */
int runtime_file_replace_host(const char *from,const char *to) {
    char detail[128];
    int e=save_replace(from,to,detail,sizeof(detail));
    if (e) fprintf(stderr,"Runtime: %s not replaced (%s%s%s); the old one is kept\n",to,strerror(e),detail[0] ? ", " : "",detail);
    return e;
}
void runtime_file_report(void) {
    printf("Runtime: files opened=%zu, reads=%zu (%llu bytes), writes=%zu, not found=%zu\n",
           opens,reads,(unsigned long long)bytes_read,writes,missing);
}

