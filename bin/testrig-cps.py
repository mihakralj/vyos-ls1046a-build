#!/usr/bin/env python3
"""testrig-cps.py: new connections per second through the DUT (the ASK1-vs-ASK2 connection-rate test).

  serve PORT [PROCS]                   dell2: minimal HTTP responder on PORT (new connections) and PORT+1
                                       (held connections), one SO_REUSEPORT process per CPU.
  run SRC DST PORT RATE SECS [HOLD] [PROCS]
                                       dell1: RATE new connections/s for SECS, each one connect, request,
                                       response, close (the server closes first). HOLD idle connections are
                                       opened first, kept for the whole run and each answers one request at
                                       the end, which proves it survived. Prints one JSON line.
  selftest                             both halves over loopback, no rig involved.

wrk is not used: it cannot pace to a fixed rate and cannot hold idle connections alongside.
"""
import asyncio, json, multiprocessing as mp, os, resource, socket, sys, time

REQ = b'GET / HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n'
RESP = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok'


def nofile():
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))


async def handle(r, w):
    try:
        await r.readuntil(b'\r\n\r\n')   # a held connection waits here until its final request
        w.write(RESP)
        await w.drain()
    except Exception:
        pass
    w.close()


def serve_proc(port):
    nofile()

    async def main():
        # host None binds 0.0.0.0 and :: separately (asyncio forces IPV6_V6ONLY on '::')
        for p in (port, port + 1):
            await asyncio.start_server(handle, None, p, reuse_port=True, backlog=65535)
        await asyncio.Event().wait()
    asyncio.run(main())


def serve(port, procs):
    ps = [mp.Process(target=serve_proc, args=(port,), daemon=True) for _ in range(procs)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()


def client_proc(i, nproc, src, dst, port, rate, secs, hold, q):
    nofile()

    async def main():
        loop = asyncio.get_running_loop()
        lat, err, held = [], {}, []

        def bad(e, pre=''):
            k = pre + type(e).__name__
            err[k] = err.get(k, 0) + 1

        async def connect(dport, timeout):
            # IP_BIND_ADDRESS_NO_PORT: the kernel picks the source port at connect() per 4-tuple, so held
            # connections (PORT+1) and new ones (PORT) share the 28k ephemeral ports instead of exhausting them
            s = socket.socket(socket.AF_INET6 if ':' in dst else socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.IPPROTO_IP, 24, 1)
                s.setblocking(False)
                s.bind((src, 0))
                await asyncio.wait_for(loop.sock_connect(s, (dst, dport)), timeout)
            except BaseException:
                s.close()
                raise
            return await asyncio.open_connection(sock=s)

        async def request(r, w):
            w.write(REQ)
            data = await asyncio.wait_for(r.read(), 3)   # until the server closes
            w.close()
            if not data.startswith(b'HTTP/1.1 200'):
                raise ConnectionError('bad response')

        async def one():
            t = loop.time()
            try:
                r, w = await connect(port, 3)
                c = loop.time() - t
                await request(r, w)
                lat.append(c)
            except Exception as e:
                bad(e)
            finally:
                sem.release()

        async def hold_one():
            try:
                held.append(await connect(port + 1, 5))
            except Exception as e:
                bad(e, 'hold_open_')

        async def check(rw):
            try:
                await request(*rw)
                return 1
            except Exception as e:
                bad(e, 'hold_end_')
                return 0

        n_hold = hold // nproc + (i < hold % nproc)
        for k in range(0, n_hold, 500):
            await asyncio.gather(*(hold_one() for _ in range(min(500, n_hold - k))))

        sem = asyncio.Semaphore(4000)   # in-flight cap per process
        per = rate / nproc
        n = int(per * secs)
        tasks, late = set(), 0
        t0 = loop.time()
        for k in range(n):
            d = t0 + k / per - loop.time()
            if d > 0:
                await asyncio.sleep(d)
            elif d < -0.1:
                late += 1               # more than 100 ms behind schedule
            await sem.acquire()
            t = asyncio.ensure_future(one())
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        await asyncio.gather(*tasks)
        elapsed = loop.time() - t0
        alive = sum(await asyncio.gather(*(check(rw) for rw in held)))
        lat.sort()
        q.put(dict(n=n, ok=len(lat), err=err, late=late, elapsed=elapsed, held=len(held), alive=alive,
                   lat=lat[::max(1, len(lat) // 20000)]))
    asyncio.run(main())


def run(src, dst, port, rate, secs, hold, procs):
    q = mp.Queue()
    ps = [mp.Process(target=client_proc, args=(i, procs, src, dst, port, rate, secs, hold, q))
          for i in range(procs)]
    for p in ps:
        p.start()
    res = [q.get() for _ in ps]
    for p in ps:
        p.join()
    err = {}
    for r in res:
        for k, v in r['err'].items():
            err[k] = err.get(k, 0) + v
    lat = sorted(x for r in res for x in r['lat'])
    pct = lambda p: round(1000 * lat[min(len(lat) - 1, int(p * len(lat)))], 3) if lat else None
    n, ok = sum(r['n'] for r in res), sum(r['ok'] for r in res)
    out = dict(rate=rate, secs=secs, attempted=n, ok=ok, failed=n - ok,
               cps=round(ok / max(r['elapsed'] for r in res)), late=sum(r['late'] for r in res),
               connect_ms_p50=pct(0.5), connect_ms_p99=pct(0.99), connect_ms_max=pct(1.0),
               hold=hold, hold_opened=sum(r['held'] for r in res), hold_alive=sum(r['alive'] for r in res),
               errors=err)
    print(json.dumps(out))
    return out


def selftest():
    srv = [mp.Process(target=serve_proc, args=(18099,), daemon=True) for _ in range(2)]
    for p in srv:
        p.start()
    time.sleep(1)
    out = run('127.0.0.1', '127.0.0.1', 18099, 2000, 2, 200, 2)
    for p in srv:
        p.terminate()
    assert out['attempted'] == 4000 and out['ok'] == out['attempted'], out
    assert out['hold_opened'] == out['hold_alive'] == 200, out
    assert 1500 < out['cps'] < 2500, out
    print('selftest OK')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['serve']:
        serve(int(a[1]), int(a[2]) if len(a) > 2 else os.cpu_count())
    elif a[:1] == ['run'] and len(a) >= 6:
        run(a[1], a[2], int(a[3]), int(a[4]), float(a[5]), int(a[6]) if len(a) > 6 else 0,
            int(a[7]) if len(a) > 7 else os.cpu_count())
    elif a[:1] == ['selftest']:
        selftest()
    else:
        sys.exit(__doc__)
