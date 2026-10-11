"""
BM25 原理演示：从零实现 + 可视化 + 实战例子
对比 Java 开发者熟悉的 Lucene 实现思路
"""
import math
from collections import Counter
from typing import List, Dict
import json


# ============================================================
# 一、从零实现 BM25（对标 Lucene 的 BM25Similarity）
# ============================================================

class BM25:
    """
    BM25 算法的纯 Python 实现。
    
    对标 Java Lucene 中的 BM25Similarity：
    - k1 对应 Lucene 的 k1 参数
    - b  对应 Lucene 的 b 参数
    """

    def __init__(self, corpus: List[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # ---- 分词（简单按空格分，生产级用 jieba / nltk） ----
        self.docs: List[List[str]] = [doc.split() for doc in corpus]
        self.N = len(self.docs)  # 总文档数

        # ---- 文档长度 ----
        self.doc_lens = [len(doc) for doc in self.docs]
        self.avgdl = sum(self.doc_lens) / self.N  # 平均文档长度

        # ---- 倒排索引：term -> {doc_id: tf} ----
        self.inverted_index: Dict[str, Dict[int, int]] = {}
        for doc_id, tokens in enumerate(self.docs):
            tf_map = Counter(tokens)
            for term, tf in tf_map.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = {}
                self.inverted_index[term][doc_id] = tf

        # ---- 每个词的 IDF（缓存） ----
        self.idf_cache: Dict[str, float] = {}

    #逆文档频率，词在越少文档中出现，IDF 越大，说明这个词越"珍贵"
    def _idf(self, term: str) -> float:
        """计算 IDF。公式等价于 Lucene 的实现。"""
        if term in self.idf_cache:
            return self.idf_cache[term]

        n_t = len(self.inverted_index.get(term, {}))  # 包含该词的文档数
        # 标准 IDF 公式（与 Lucene 一致）
        idf = math.log((self.N - n_t + 0.5) / (n_t + 0.5) + 1.0)
        self.idf_cache[term] = idf
        return idf

    def score(self, query: str, doc_id: int) -> float:
        """计算查询对单个文档的 BM25 得分"""
        query_terms = query.split()
        doc_len = self.doc_lens[doc_id]
        doc_tf_map = Counter(self.docs[doc_id])

        total = 0.0
        for term in query_terms:
            tf = doc_tf_map.get(term, 0)
            if tf == 0:
                continue

            idf = self._idf(term)

            # ---- BM25 的核心：TF 饱和 + 长度归一化 ----
            # 分母中的这部分就是长度归一化项
            length_norm = 1 - self.b + self.b * (doc_len / self.avgdl)

            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * length_norm
            total += idf * (numerator / denominator)

        return total

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """对查询返回 top-k 文档"""
        scores = []
        for doc_id in range(self.N):
            s = self.score(query, doc_id)
            if s > 0:
                scores.append({
                    "doc_id": doc_id,
                    "score": round(s, 4),
                    "text": " ".join(self.docs[doc_id])
                })
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]


# ============================================================
# 二、具体例子：小型文档集
# ============================================================

def demo_basic():
    """基础例子：展示 BM25 的核心行为"""
    print("=" * 60)
    print("📚 DEMO 1: 基础检索例子")
    print("=" * 60)

    corpus = [
        "苹果 手机 很 好用",                      # doc0
        "苹果 香蕉 都是 水果 很 好吃",            # doc1
        "苹果 公司 发布 了 新 手机",              # doc2
        "香蕉 是 一种 水果",                      # doc3
        "手机 市场 竞争 激烈 苹果 手机 领先",     # doc4
    ]

    bm25 = BM25(corpus, k1=1.5, b=0.75)

    print("\n📊 文档统计：")
    print(f"  文档总数 N = {bm25.N}")
    print(f"  平均文档长度 avgdl = {bm25.avgdl:.1f}")
    print(f"\n  IDF 值：")
    for term in ["苹果", "手机", "香蕉", "水果"]:
        print(f"    IDF('{term}') = {bm25._idf(term):.4f}  "
              f"(出现在 {len(bm25.inverted_index.get(term, {}))} 篇文档中)")

    queries = ["苹果", "手机", "苹果 手机", "香蕉"]
    for q in queries:
        print(f"\n🔍 查询: \"{q}\"")
        results = bm25.search(q, top_k=3)
        for r in results:
            print(f"  doc{r['doc_id']} | score={r['score']:.4f} | \"{r['text']}\"")


# ============================================================
# 三、参数行为可视化
# ============================================================

def demo_parameters():
    """展示 k1 和 b 参数的行为"""
    print("\n" + "=" * 60)
    print("📚 DEMO 2: 参数 k1 和 b 的影响")
    print("=" * 60)

    # 构造一个场景：
    # - 查询词在短文档出现 5 次 vs 在长文档出现 5 次
    # - 展示 b 如何惩罚长文档
    # - 展示 k1 如何让 TF 饱和

    print("""
    ┌─────────────────────────────────────────────────────┐
    │  场景：查询词 "python" 在两个文档中：                │
    │    doc_short: "python" 出现 5 次，文档长度 10 词    │
    │    doc_long:  "python" 出现 5 次，文档长度 100 词   │
    │  直觉：短文档中 5 次更"集中"，应得分更高             │
    │  b 越大 → 长度惩罚越强 → 短文档得分优势更大         │
    └─────────────────────────────────────────────────────┘
    """)

    # 展示 TF 饱和曲线
    print("\n📈 TF 饱和曲线（k1 的影响）：")
    print("  公式: TF_saturated = tf * (k1+1) / (tf + k1)")
    print(f"  {'tf':<6} {'k1=0.5':>8} {'k1=1.2':>8} {'k1=2.0':>8}")
    for tf in range(0, 16):
        row = f"  {tf:<6}"
        for k1 in [0.5, 1.2, 2.0]:
            saturated = tf * (k1 + 1) / (tf + k1)
            row += f" {saturated:>8.3f}"
        print(row)
    print("  结论：k1 越小，TF 增长越快饱和（出现几次就到上限了）")

    # 展示长度归一化
    print("\n📏 长度归一化（b 的影响）：")
    print("  公式: length_norm = 1 - b + b * (doc_len / avgdl)")
    avgdl = 50
    for label, doc_len in [("短文档", 10), ("平均文档", 50), ("长文档", 200)]:
        row = f"  {label}(len={doc_len}):"
        for b_val in [0.0, 0.5, 0.75, 1.0]:
            norm = 1 - b_val + b_val * (doc_len / avgdl)
            row += f"  b={b_val}: {norm:.3f}"
        print(row)
    print("  结论：b=0 无惩罚；b=1 完全按比例惩罚；b=0.75 是折中")


# ============================================================
# 四、BM25 vs TF-IDF 对比
# ============================================================

def demo_vs_tfidf():
    """展示 BM25 相比 TF-IDF 的改进"""
    print("\n" + "=" * 60)
    print("📚 DEMO 3: BM25 vs TF-IDF 对比")
    print("=" * 60)

    print("""
    ┌────────────────────────────────────────────────────┐
    │  TF-IDF 的问题：                                    │
    │    score = IDF * tf                                │
    │    1. tf 线性增长：出现 100 次得分是 1 次的 100 倍 │
    │    2. 不惩罚长文档：100 页论文和 1 页笔记同等对待   │
    │                                                     │
    │  BM25 的改进：                                      │
    │    1. tf 饱和：词频翻倍 ≠ 得分翻倍                 │
    │    2. 长度惩罚：短文档中相同词频更有价值            │
    └────────────────────────────────────────────────────┘
    """)

    # 构造极端例子
    corpus = [
        "python python python python python python python python python python",  # 10次
        "python java c++ rust go scala kotlin swift ruby typescript python",      # 2次python
        "hello world",                                                             # 0次
    ]

    bm25 = BM25(corpus, k1=1.5, b=0.75)

    print("文档：")
    for i, doc in enumerate(corpus):
        print(f"  doc{i}: \"{doc}\" (长度={len(doc.split())})")

    print(f"\n查询: 'python'")
    for i in range(3):
        score = bm25.score("python", i)
        tf = Counter(corpus[i].split()).get("python", 0)
        idf = bm25._idf("python")
        tfidf = idf * tf
        print(f"  doc{i}: BM25={score:.4f}  |  TF-IDF={tfidf:.4f}  |  tf={tf}")


# ============================================================
# 五、实战：用 BM25 做 RAG 检索（Python 生态常用方式）
# ============================================================

def demo_rag_style():
    """模拟 RAG 系统中用 BM25 检索文档片段"""
    print("\n" + "=" * 60)
    print("📚 DEMO 4: RAG 场景 — BM25 检索知识库")
    print("=" * 60)

    knowledge_base = [
        "Python 是 一门 解释型 编程语言 适合 数据科学",
        "Python 的 GIL 全局解释器锁 限制了 多线程 性能",
        "Java 的 JVM 支持 真正 的 多线程 并发",
        "Python 中 使用 asyncio 可以 实现 协程 并发",
        "Java 和 Python 都是 面向对象 编程语言",
        "Python 的 列表推导式 是 一种 简洁 的 语法",
        "Java 的 Stream API 类似 Python 的 列表推导式",
        "Python 中 None 类似 Java 中 null",
    ]

    bm25 = BM25(knowledge_base)

    queries = [
        "Python 多线程 性能",
        "Python Java 面向对象",
        "Python 并发",
    ]

    for q in queries:
        print(f"\n🔍 用户问题: \"{q}\"")
        results = bm25.search(q, top_k=3)
        for r in results:
            print(f"  [{r['score']:.4f}] → \"{r['text']}\"")


# ============================================================
# 六、运行所有 Demo
# ============================================================

if __name__ == "__main__":
    demo_basic()
    demo_parameters()
    demo_vs_tfidf()
    demo_rag_style()

    print("\n" + "=" * 60)
    print("✅ 所有 Demo 运行完毕")
    print("=" * 60)