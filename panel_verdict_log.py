"""One append boundary for panel and Studio verdicts; never rewrites old bytes."""
import fcntl
import json
import os
from pathlib import Path

class Conflict(OSError):pass

def parsed(raw):
    rows=[]
    for line in raw.splitlines():
        try:
            value=json.loads(line)
        except (ValueError,UnicodeError):
            continue
        if isinstance(value,dict) and value.get('id'):
            rows.append(value)
    return rows

def read_rows(path):
    try:
        return parsed(Path(path).read_bytes())
    except OSError:
        return []

def durable(fd,parent):
    os.fsync(fd)
    directory=os.open(parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)

def append(path,row,request_id=None):
    """All current canonical producers share this descriptor lock.

    An interrupted row stays in place. The next producer isolates its fragment
    before appending, and ASCII JSON means a short write cannot split UTF-8.
    """
    path=Path(path)
    if not isinstance(row,dict) or not row.get('id'):
        raise ValueError('A verdict identity is required')
    raw=json.dumps(row,ensure_ascii=True,separators=(',',':')).encode('ascii')+b'\n'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        if request_id is not None:
            if row.get('request_id')!=request_id:raise Conflict('Request identity differs')
            os.lseek(fd,0,os.SEEK_SET);chunks=[]
            while True:
                chunk=os.read(fd,262144)
                if not chunk:break
                chunks.append(chunk)
            matches=[r for r in parsed(b''.join(chunks)) if r.get('request_id')==request_id]
            if matches:
                if len(matches)!=1 or any(matches[0].get(k)!=row.get(k) for k in ('id','verdict','note','card_sha256')):
                    raise Conflict('Request already belongs to different feedback')
                durable(fd,path.parent)
                return matches[0]
        size=os.lseek(fd,0,os.SEEK_END)
        if size and os.pread(fd,1,size-1)!=b'\n':
            if os.write(fd,b'\n')!=1:raise OSError('Cannot isolate interrupted verdict; retain the draft')
            os.fsync(fd)
        if os.write(fd,raw)!=len(raw):raise OSError('Verdict write was incomplete; retain the draft')
        os.fsync(fd)
        end=os.lseek(fd,0,os.SEEK_CUR)
        saved=os.pread(fd,len(raw),end-len(raw))
        if saved!=raw or parsed(saved)!=[row]:raise OSError('Canonical verdict readback was not confirmed')
        durable(fd,path.parent)
        return row
    finally:
        os.close(fd)
