"""Finite lock model; an abstraction, not a proof about all firmware execution."""
from collections import deque
import unittest

gb=(('a','G'),('a','B'),('r','B'),('a','E'),('r','E'),('r','G'))
pw=(('a','B'),('r','B'))
def query(n,fixed):
    tail=gb+(('a','E'),('r','E')) if fixed else (('a','E'),)+gb+(('r','E'),)
    return pw+(gb+pw if n==8 else ())+tail

def explore(programs):
    first=(tuple(0 for _ in programs),(None,None,None),(0,0,0))
    order={'G':0,'B':1,'E':2}
    seen={first}; pending=deque([first]); deadlocks=0; completed=0; witness=None
    while pending:
        state=pending.popleft(); pcs,owners,depths=state; successors=[]
        if all(pc==len(programs[t]) for t,pc in enumerate(pcs)):
            completed+=1;continue
        for t,pc in enumerate(pcs):
            if pc==len(programs[t]):continue
            op,lock=programs[t][pc]; lock=order[lock]
            if op=='a' and owners[lock] not in (None,t):continue
            nxtpcs=list(pcs); nxtowners=list(owners); nxtdepths=list(depths)
            nxtpcs[t]+=1
            if op=='a': nxtowners[lock]=t; nxtdepths[lock]+=1
            else:
                assert owners[lock]==t and depths[lock]>0
                nxtdepths[lock]-=1
                if nxtdepths[lock]==0:nxtowners[lock]=None
            nxt=(tuple(nxtpcs),tuple(nxtowners),tuple(nxtdepths)); successors.append(nxt)
            if nxt not in seen:seen.add(nxt);pending.append(nxt)
        if not successors:
            deadlocks+=1;witness=state
        assert len(seen)<2000000,'Model bound exceeded'
    return {'states':len(seen),'deadlocks':deadlocks,'completed':completed,'witness':witness}


class LockModelTests(unittest.TestCase):
    def test_known_cycle_removed(self):
        for names in ((8,"battery"),(9,"battery"),(8,9),(8,8),(9,9),(8,9,"battery"),(8,8,"battery"),(9,9,"battery"),(8,9,"battery","battery")):
            for fixed in (False,True):
                result=explore([gb if n=="battery" else query(n,fixed) for n in names])
                with self.subTest(tasks=names,fixed=fixed):
                    if fixed or names==(9,9): self.assertEqual(result["deadlocks"],0)
                    else: self.assertGreater(result["deadlocks"],0)
                    self.assertGreater(result["completed"],0)
