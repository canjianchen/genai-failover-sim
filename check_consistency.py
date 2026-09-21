import json, sys


def flatten(x, prefix=""):
    if isinstance(x, dict):
        for k in sorted(x):
            yield from flatten(x[k], f"{prefix}/{k}")
    elif isinstance(x, list):
        for i,v in enumerate(x):
            yield from flatten(v, f"{prefix}/{i}")
    else:
        yield prefix, x


def main():
    if len(sys.argv) != 3:
        print("usage: check_consistency.py FROZEN.json REGEN.json", file=sys.stderr)
        return 2
    with open(sys.argv[1]) as f: a=json.load(f)
    with open(sys.argv[2]) as f: b=json.load(f)
    fa=dict(flatten(a)); fb=dict(flatten(b))
    keys=sorted(set(fa)|set(fb))
    mismatches=[]
    for k in keys:
        if fa.get(k) != fb.get(k):
            mismatches.append((k,fa.get(k),fb.get(k)))
    if mismatches:
        print(f"{len(mismatches)} mismatches")
        for row in mismatches[:25]: print(row)
        return 1
    print(f"{len(keys)} values compared, 0 mismatches")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
