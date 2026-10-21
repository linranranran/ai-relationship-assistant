"""检查当前展示入口的相对链接与依赖清单；历史笔记不作为当前接口合同。"""
from pathlib import Path
import re
import tomllib
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
ENTRIES=['README.md','frontend/README.md','docs/README.md','docs/GETTING_STARTED.md',
         'docs/ARCHITECTURE.md','docs/DEMO.md','docs/evaluations/showcase/README.md',
         'examples/README.md','scripts/evaluation/README.md']


def main():
    broken=[]
    for name in ENTRIES:
        path=ROOT/name
        for target in re.findall(r'\[[^\]\n]+\]\(([^)\n]+)\)',path.read_text(encoding='utf-8')):
            target=target.strip('<>').split('#',1)[0]
            if not target or re.match(r'^[a-z]+:',target):continue
            if not (path.parent/unquote(target)).exists():broken.append((name,target))
    assert not broken,broken
    deps=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies']
    requirements=[line.strip() for line in (ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines()
                  if line.strip() and not line.startswith('#')]
    assert deps==requirements,'requirements.txt must match pyproject.toml dependencies'
    assert '知客-销售沟通助手' in (ROOT/'frontend/index.html').read_text(encoding='utf-8')
    print({'current_documents_checked':len(ENTRIES),'broken_links':0,'dependency_lists_aligned':True})


if __name__=='__main__':
    main()
