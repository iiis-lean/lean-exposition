"""Handwritten bilingual orthogonal projection example using real Reader packages.

The mathematics is elementary and exact. Lean snippets are illustrative source,
not a claim of successful compilation. Run with an output directory outside repo.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

from lean_exposition.models.facts import (
    DeclContent, DeclRef, Dependency, Provenance, RawDecl, Repository, Scope,
    Status, TextContent, Workspace, WorkspaceManifest,
)
from lean_exposition.exposition import ContentStore

# Each row keeps the two languages beside the same mathematical object.
ROWS = [
 ('projection', 'Projection matrix', '投影矩阵', [],
  r'Let $P=\frac12\begin{pmatrix}1&1\\1&1\end{pmatrix}$. For $x=(a,b)^\mathsf T$, $Px=\frac{a+b}{2}(1,1)^\mathsf T$.',
  r'令 $P=\frac12\begin{pmatrix}1&1\\1&1\end{pmatrix}$。对 $x=(a,b)^\mathsf T$，有 $Px=\frac{a+b}{2}(1,1)^\mathsf T$。',
  '', '', 'def project (x : ℝ × ℝ) : ℝ × ℝ :=\n  ((x.1 + x.2) / 2, (x.1 + x.2) / 2)'),
 ('residual', 'Residual', '正交余量', ['projection'],
  r'The complementary matrix is $Q=I-P=\frac12\begin{pmatrix}1&-1\\-1&1\end{pmatrix}$.',
  r'补矩阵为 $Q=I-P=\frac12\begin{pmatrix}1&-1\\-1&1\end{pmatrix}$。',
  r'Thus $Qx=\frac{a-b}{2}(1,-1)^\mathsf T$, perpendicular to the diagonal.',
  r'因此 $Qx=\frac{a-b}{2}(1,-1)^\mathsf T$，其方向垂直于对角线。',
  'def residual (x : ℝ × ℝ) : ℝ × ℝ :=\n  (x.1 - (project x).1, x.2 - (project x).2)'),
 ('idempotent', 'Projecting twice', '投影的幂等性', ['projection'],
  r'$P^2=P$: applying the projection twice has the same effect as applying it once.',
  r'$P^2=P$：连续投影两次，与投影一次的结果相同。',
  r'Both coordinates of $Px$ equal $m=(a+b)/2$. Their average is again $m$. Equivalently, $$P^2=\frac14\begin{pmatrix}2&2\\2&2\end{pmatrix}=P.$$ Every point on the diagonal is therefore fixed by $P$.',
  r'$Px$ 的两个坐标均为 $m=(a+b)/2$，它们的平均值仍是 $m$。等价地，$$P^2=\frac14\begin{pmatrix}2&2\\2&2\end{pmatrix}=P.$$ 因而对角线上的每一点都被 $P$ 固定。',
  'theorem project_twice (x : ℝ × ℝ) :\n    project (project x) = project x := by\n  ext <;> simp [project] <;> ring'),
 ('orthogonal', 'Orthogonal directions', '两个方向正交', ['projection','residual'],
  r'For every $x$, $\langle Px,Qx\rangle=0$.',
  r'对任意 $x$，有 $\langle Px,Qx\rangle=0$。',
  r'Writing $m=(a+b)/2$ and $d=(a-b)/2$, the vectors are $(m,m)$ and $(d,-d)$. Their inner product is $md-md=0$. In matrix form, $P^\mathsf TQ=PQ=0$.',
  r'记 $m=(a+b)/2$、$d=(a-b)/2$，两个向量分别为 $(m,m)$ 和 $(d,-d)$。内积是 $md-md=0$。用矩阵表示，即 $P^\mathsf TQ=PQ=0$。',
  'def dot (x y : ℝ × ℝ) : ℝ := x.1*y.1 + x.2*y.2\n\ntheorem project_orthogonal (x : ℝ × ℝ) :\n    dot (project x) (residual x) = 0 := by\n  simp [dot, project, residual]\n  ring'),
 ('decomposition', 'Unique decomposition', '唯一正交分解', ['projection','residual','idempotent','orthogonal'],
  r'Every vector splits uniquely into a diagonal and an anti-diagonal part: $$\begin{pmatrix}a\\b\end{pmatrix}=\frac{a+b}{2}\begin{pmatrix}1\\1\end{pmatrix}+\frac{a-b}{2}\begin{pmatrix}1\\-1\end{pmatrix}.$$',
  r'每个向量都能唯一分解为对角线方向和反对角线方向的两部分：$$\begin{pmatrix}a\\b\end{pmatrix}=\frac{a+b}{2}\begin{pmatrix}1\\1\end{pmatrix}+\frac{a-b}{2}\begin{pmatrix}1\\-1\end{pmatrix}。$$',
  r'Existence follows from $P+Q=I$. For uniqueness, a vector in both lines must have the form $(t,t)=(s,-s)$; hence $t=s=-s$, so $s=t=0$. Subtracting any two decompositions reduces to this intersection argument.',
  r'存在性来自 $P+Q=I$。若一个向量同时属于两条直线，则 $(t,t)=(s,-s)$，从而 $t=s=-s$，所以 $s=t=0$。将任意两组分解相减，就归结为这个交集论证。',
  'theorem split_coordinates (a b : ℝ) :\n    a = (a+b)/2 + (a-b)/2 ∧\n    b = (a+b)/2 - (a-b)/2 := by\n  constructor <;> ring'),
 ('pythagoras', 'Energy splits', '平方范数分解', ['decomposition','orthogonal'],
  r'The orthogonal pieces satisfy $\|x\|^2=\|Px\|^2+\|Qx\|^2$.',
  r'这两个正交分量满足 $\|x\|^2=\|Px\|^2+\|Qx\|^2$。',
  r'Expand $\langle Px+Qx,Px+Qx\rangle$. The two cross terms vanish by orthogonality, leaving precisely the two squared norms. In coordinates, $$a^2+b^2=\frac{(a+b)^2}{2}+\frac{(a-b)^2}{2}.$$ For $x=(3,1)^\mathsf T$, the pieces are $(2,2)^\mathsf T$ and $(1,-1)^\mathsf T$: $10=8+2$.',
  r'展开 $\langle Px+Qx,Px+Qx\rangle$，两个交叉项因正交而消失，剩下的正是两个平方范数。用坐标写出，$$a^2+b^2=\frac{(a+b)^2}{2}+\frac{(a-b)^2}{2}。$$ 对 $x=(3,1)^\mathsf T$，分量为 $(2,2)^\mathsf T$ 和 $(1,-1)^\mathsf T$，于是 $10=8+2$。',
  'theorem energy_split (a b : ℝ) :\n    a^2 + b^2 = (a+b)^2/2 + (a-b)^2/2 := by\n  ring'),
 ('distance', 'Distance identity', '距离恒等式', ['projection','residual','orthogonal','pythagoras'],
  r'For a point $y=(t,t)^\mathsf T$ on the diagonal, $$\|x-y\|^2=\frac{(a-b)^2}{2}+2\left(t-\frac{a+b}{2}\right)^2.$$',
  r'对角线上的点 $y=(t,t)^\mathsf T$ 满足 $$\|x-y\|^2=\frac{(a-b)^2}{2}+2\left(t-\frac{a+b}{2}\right)^2。$$',
  r'The displacement is $x-y=Qx+(Px-y)$. Its first term is anti-diagonal; its second is diagonal. Pythagoras separates the fixed residual from the part depending on $t$.',
  r'位移可以写成 $x-y=Qx+(Px-y)$。第一项沿反对角线方向，第二项沿对角线方向。勾股恒等式将固定余量与依赖 $t$ 的部分分离。',
  'theorem distance_identity (a b t : ℝ) :\n    (a-t)^2 + (b-t)^2 =\n      (a-b)^2/2 + 2*(t-(a+b)/2)^2 := by\n  ring'),
 ('nearest', 'Nearest point', '最近点', ['projection','idempotent','decomposition','distance'],
  r'$Px$ is the unique point on the diagonal nearest to $x$. The minimum squared distance is $(a-b)^2/2$.',
  r'$Px$ 是对角线上距离 $x$ 最近的唯一一点。最小距离的平方为 $(a-b)^2/2$。',
  r'The second term in the distance identity is nonnegative, and vanishes exactly when $t=(a+b)/2$. Thus every other diagonal point is strictly farther away. For $(3,1)^\mathsf T$, the closest point is $(2,2)^\mathsf T$ and the distance is $\sqrt2$. This is the simplest least-squares problem: replacing two observations by their common mean.',
  r'距离恒等式中的第二项非负，且当且仅当 $t=(a+b)/2$ 时为零。因此其余对角线上的点都严格更远。对 $(3,1)^\mathsf T$，最近点是 $(2,2)^\mathsf T$，距离为 $\sqrt2$。这也是最简单的最小二乘问题：用共同的均值替代两个观测值。',
  'theorem nearest_bound (a b t : ℝ) :\n    (a-b)^2/2 ≤ (a-t)^2 + (b-t)^2 := by\n  rw [distance_identity]\n  nlinarith [sq_nonneg (t-(a+b)/2)]'),
]

REGIONS = [
 ('setup',['projection','residual'],'Two complementary maps','两个互补映射',r'We project onto the line $a=b$.',r'考虑到直线 $a=b$ 的投影。',r'The matrices $P$ and $Q=I-P$ isolate the diagonal and anti-diagonal directions.',r'矩阵 $P$ 与 $Q=I-P$ 分别提取对角线和反对角线方向。'),
 ('geometry',['idempotent','orthogonal'],'Projection geometry','投影的几何性质',r'The two maps have a simple geometric meaning.',r'两个映射具有直观的几何意义。',r'$P^2=P$ and $PQ=0$: projected vectors are fixed, and the residual is perpendicular.',r'$P^2=P$ 且 $PQ=0$：投影后的向量保持不变，余量与投影正交。'),
 ('split',['decomposition','pythagoras'],'Decomposition and energy','分解与平方范数',r'The algebra now becomes an orthogonal decomposition.',r'上述代数关系给出了正交分解。',r'Every vector has a unique split $x=Px+Qx$, with $\|x\|^2=\|Px\|^2+\|Qx\|^2$.',r'每个向量都唯一地分解为 $x=Px+Qx$，且 $\|x\|^2=\|Px\|^2+\|Qx\|^2$。'),
 ('optimal',['distance','nearest'],'The closest point','寻找最近点',r'Which point on the diagonal best approximates $x$?',r'对角线上的哪个点最接近 $x$？',r'The distance splits into a fixed residual and a nonnegative square. Its unique minimizer is $Px$.',r'距离的平方分成固定余量与一个非负平方项，因此唯一的最小值点是 $Px$。'),
]

def build(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    prov=(Provenance('handwritten_fixture','orthogonal-projection; illustrative Lean snippets, not compilation-verified'),)
    text=lambda value:TextContent(value,'present',prov)
    refs={row[0]:DeclRef('projection',row[0]) for row in ROWS}
    decls=[]
    for id,en,zh,deps,stmt,zstmt,proof,zproof,code in ROWS:
        decls.append(RawDecl(refs[id],id,'Projection','scope','def' if id in ('projection','residual') else 'theorem',DeclContent(text(stmt),text(code),tuple(Dependency(refs[d],'text_reference',prov) for d in deps)),Status('fixture',prov),prov,proof=DeclContent(text(proof),TextContent(None,"missing",prov,reason="Proof is included in the full declaration source.")) if proof else None))
    ws=Workspace(WorkspaceManifest((Repository('projection','leanprover/lean4:v4.28.0','scope',revision='b'*40,primary_outcomes=(refs['nearest'],)),),()),tuple(decls),(Scope('scope','projection','repository','Projection',prov),))
    def node(id,kind,parent,children,names,title):return dict(id=id,kind=kind,parent=parent,children=children,decl_refs=[asdict(refs[n]) for n in names],title=title,representative=asdict(refs[names[0]]) if kind=='unit' else None,source_scope='scope')
    nodes=[node('root','repo',None,[r[0] for r in REGIONS],list(refs),'Orthogonal projection onto a line')]
    for id,children,en,*_ in REGIONS:
        nodes.append(node(id,'region','root',children,children,en))
        nodes.extend(node(child,'unit',id,[],[child],next(row[1] for row in ROWS if row[0]==child)) for child in children)
    encoded=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False).encode()
    config={'origin':'handwritten_projection_fixture'}
    hierarchy=dict(hierarchy_id='',workspace_digest=ws.digest(),source_digest=hashlib.sha256(encoded(config)).hexdigest(),config_digest=hashlib.sha256(encoded(config)).hexdigest(),repo_key='projection',root_id='root',config=config,diagnostics=[],nodes=nodes,edges=[dict(id=f'{dep}-{row[0]}',provider_node=dep,consumer_node=row[0],provider_decl=asdict(refs[dep]),consumer_decl=asdict(refs[row[0]])) for row in ROWS for dep in row[3]],external_refs=[])
    identity=dict(hierarchy);identity.pop('hierarchy_id');hierarchy['hierarchy_id']='hierarchy:'+hashlib.sha256(encoded(identity)).hexdigest()[:24]
    (directory/'workspace.json').write_text(ws.to_json());(directory/'hierarchy.json').write_bytes(encoded(hierarchy))
    packages=[]
    for locale in ('en','zh'):
        zh=locale=='zh'
        blocks={'root':dict(lead_in=r'给定平面向量 $x=(a,b)^\mathsf T$，我们要找到直线 $a=b$ 上距离它最近的点。' if zh else r'Given a plane vector $x=(a,b)^\mathsf T$, we seek its nearest point on the line $a=b$.',synopsis=r'投影矩阵 $$P=\frac12\begin{pmatrix}1&1\\1&1\end{pmatrix}$$ 把 $x$ 映到 $(\frac{a+b}{2},\frac{a+b}{2})^\mathsf T$。证明的关键是把 $x$ 分成两个正交部分，再用勾股恒等式比较距离。' if zh else r'The projection matrix $$P=\frac12\begin{pmatrix}1&1\\1&1\end{pmatrix}$$ sends $x$ to $(\frac{a+b}{2},\frac{a+b}{2})^\mathsf T$. The proof splits $x$ into orthogonal pieces, then compares distances using Pythagoras.',lead_out=r'这个二维例子将矩阵的幂等性、正交分解和最小二乘联系在一起。' if zh else 'This two-dimensional example connects idempotent matrices, orthogonal decomposition and least squares.',anchors=[])}
        titles={'root':'到直线的正交投影' if zh else 'Orthogonal projection onto a line'}
        for id,children,en,zname,intro,zintro,syn,zsyn in REGIONS:
            blocks[id]=dict(lead_in=zintro if zh else intro,synopsis=zsyn if zh else syn,lead_out=({'setup':('Together these maps recover the original vector.','这两个映射之和恢复原向量。'),'geometry':('The two directions are perpendicular.','两个方向彼此垂直。'),'split':('No squared length is lost in the decomposition.','分解前后的平方范数总和不变。'),'optimal':('The mean gives the unique best approximation.','均值给出唯一的最佳逼近。')}[id][1 if zh else 0]),anchors=[]);titles[id]=zname if zh else en
        for id,en,zname,deps,stmt,zstmt,proof,zproof,code in ROWS:
            blocks[id]=dict(statement=zstmt if zh else stmt,proof=zproof if zh else proof,anchors=[]) if id not in ('projection','residual') else dict(content=(zstmt+'\n\n'+zproof) if zh else (stmt+'\n\n'+proof),anchors=[]);titles[id]=zname if zh else en
        store=ContentStore(ws,hierarchy,directory/f'content.{locale}.json',locale=locale)
        store.state['metadata']={id:dict(title=title,short_description=next((r[7] if zh else r[6] for r in REGIONS if r[0]==id),'')) for id,title in titles.items()}
        store.publish(blocks)
        packages.append(dict(workspace='workspace.json',hierarchy='hierarchy.json',content=f'content.{locale}.json'))
    (directory/'packages.json').write_bytes(encoded(packages))

if __name__=='__main__':build(sys.argv[1])
