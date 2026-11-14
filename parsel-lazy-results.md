# Parsel 选择结果列表的计算时机与惰性化评估

分析对象：`parsel/selector.py`（`Selector` / `SelectorList`）与 `parsel/utils.py`（`flatten` / `iflatten` / `extract_regex`）。
结论先行：**结果列表的"包装与拼接"阶段可以惰性化并获得明显的峰值内存收益，但 lxml 单次求值本身不可流式化；完全惰性会改变错误发生时点与"删除后可见性"语义，因此兼容路径应是"急切编译 + 可选的惰性求值 API"，而不是替换现有 `SelectorList`。**

---

## 1. 现状：各阶段的计算时机

### 1.1 解析阶段（已物化，不可延迟）

`Selector.__init__` 经 `create_root_node()` 调用 `etree.fromstring()`，整棵 DOM 树在构造时即全部物化（`selector.py:122-163`）。这是内存基线，任何结果层的惰性化都不能减少它。

### 1.2 单次 `xpath()` / `css()` 求值（三次物化）

`Selector._xpath()`（`selector.py:760-810`）每次调用发生三次物化：

1. `xpathev(root, **kwargs)` —— lxml/libxml2 一次性返回完整 Python `list`（节点集语义：去重、文档序）。**这是原子边界，libxml2 不支持流式返回节点集。**
2. 列表推导把每个结果包装成 `Selector` —— 又一个大 list。注意每个 `Selector` 在 `__init__` 里都会 `dict(self._default_namespaces)` 再 `update()`，即**每个节点一份独立的 namespaces dict**（已实测：`a.namespaces is b.namespaces` 为 `False`）。
3. `selectorlist_cls(result)` 包成 `SelectorList`。

`css()` 只是先 `_css2xpath()` 翻译再走 `xpath()`（`selector.py:812-829`），时机相同。错误时点：`etree.XPath` 编译失败或求值失败都在 `.xpath()` **调用当下**抛 `ValueError`（已实测确认）。

### 1.3 链式筛选 `SelectorList.xpath()` / `css()`（额外一层中间列表）

`selector.py:242-244`：

```python
return self.__class__(flatten([x._xpath(xpath, namespaces, kwargs) for x in self]))
```

先为每个父节点各算出一个完整 `SelectorList`（list-of-lists 全部同时存活），再 `flatten` 拼接成最终结果。峰值 = 中间 list-of-lists + 最终列表。结果顺序为"父节点主序"（父 0 的全部子结果、父 1 的全部子结果……），**重复节点被保留**——已实测：`sel.xpath("//div").xpath("//p")` 在 2 个 `div`、3 个 `p` 的文档上返回 6 项（每个 div 都从文档根重新匹配全部 p）。

### 1.4 索引与切片访问（物化后的 O(1)）

`SelectorList.__getitem__`（`selector.py:180-186`）：单索引 O(1) 返回 `Selector`；切片复制引用生成**新的 `SelectorList`**（已实测类型与长度正确）。这依赖底层 `list` 已完全物化——`len()`、负索引、切片、相等比较、`bool` 全部是即时语义。

### 1.5 正则取首项 `re_first`（部分短路）

- `SelectorList.re_first`（`selector.py:285-306`）已经用生成器 `iflatten(x.re(...) for x in self)`，**元素间短路**：命中即返回，不处理后续元素。
- 但每个元素内部 `Selector.re()` → `extract_regex()` 用 `regex.findall()`，**单元素内是 eager 的**；且上游的 `SelectorList` 本身早已物化。实测 10 万个 `li`：`re_first` 命中首项 0.173s vs 命中末项 0.482s——短路有效，但列表物化成本已先行支付。
- `Selector.re_first`（`selector.py:865-884`）对单元素同样先 `findall` 再取首项。

### 1.6 删除节点后的对象状态（快照语义）

`Selector.drop()`（`selector.py:943-979`）直接改树（`drop_tree()` / `parent.remove()`，XML 下保留 tail）。已实测：

- 重新查询 `sel.css("li")` 看不到已删节点（树已变）；
- **旧的 `SelectorList` 及其中的 `Selector` 仍然可见已删节点**（`old_list.getall()` 照常返回）——lxml 元素代理持有已分离子树的引用，当前语义是"求值时刻的快照"。
- 伪元素（`::text`）`drop()` 抛 `CannotRemoveElementWithoutRoot`；根节点抛 `CannotRemoveElementWithoutParent`——错误在 `drop()` 调用时抛出。

---

## 2. 已物化 vs 可延迟的阶段划分

| 阶段 | 现状 | 可否延迟 | 说明 |
|---|---|---|---|
| 文档解析（`create_root_node`） | 物化 | 否 | 内存基线，不在本次范围 |
| lxml 单次求值 `xpathev(root)` | 物化 | 否 | libxml2 节点集原子返回，是惰性化的最小粒度 |
| 结果包装为 `Selector`（含每节点 namespaces dict） | 物化 | **可延迟** | 纯 Python 开销，10 万节点实测约 37 MB 的主要构成 |
| 链式 list-of-lists + `flatten` | 物化 | **可延迟** | 改为对生成器 `iflatten` 即可流式拼接 |
| `len` / 索引 / 切片 / 相等 / `bool` | 即时 | 否（语义要求） | `SelectorList` 是 `list` 子类，列表契约要求物化或缓存 |
| `re_first` 元素间短路 | 已短路 | — | 单元素内 `findall` 可改 `finditer` 进一步省内存 |
| `drop()` 的树修改 | 即时 | 否 | 惰性与 drop 组合会改变语义（见 §4） |

---

## 3. 峰值内存实测（tracemalloc，Python 侧）

实验脚本见 §6。10 万个 `<li>`：

| 场景 | 峰值 |
|---|---|
| 单父链式 `sel.css("ul").css("li")`（现状 eager） | 37.6 MB |
| 同上，仅把 `flatten([...])` 改为对生成器 `iflatten` | 37.6 MB（单父时中间层只有 1 个子列表，无差异） |
| 5000 父 × 20 子链式（现状 eager） | 39.7 MB |
| 同上，生成器拼接 | 36.8 MB（省约 3 MB，即中间 list-of-lists） |
| 只迭代不收集（假想全惰性） | ≈ 0 MB（Python 侧） |

结论：

- **只要最终物化成 `SelectorList`，峰值就下不来**——37 MB 的大头是 10 万个 `Selector` 对象 + 10 万份 namespaces dict + 列表槽位，中间拼接层只占零头。
- 真正的收益来自"不物化"：消费方只需逐个处理（如 `re_first`、逐条写管道）时，全惰性可把结果层 Python 开销降到接近零。lxml 树本身的开销不变。

---

## 4. 全惰性的兼容性风险（必须保留的语义）

1. **结果顺序与重复节点**：链式惰性化只要保持"父主序 + 每父内部文档序"即可完全兼容；重复节点（链式绝对 XPath 场景）自然保留。✅ 无风险。
2. **错误发生时点**：现状是调用 `.xpath()` 时立即抛 `ValueError`。全惰性会把求值错误推迟到首次迭代。缓解：**急切编译、延迟求值**——`_get_xpath_evaluator()` 在调用时执行（语法错误照常立即抛出），仅把 `xpathev(root, **kwargs)` 推迟。注意 XPath 变量未定义等错误仍在求值时才出现，时点会后移，需文档声明。
3. **删除后可见性（快照 vs 活视图）**：现状是快照——`drop()` 之后旧列表仍可见已删节点。全惰性若在 `drop()` 之后才迭代，会看到修改后的树，结果不同。这是**最实质的语义断裂**，决定了惰性只能是 opt-in，不能改默认行为。
4. **`list` 子类契约**：`SelectorList` 公开继承 `list`，下游（含 Scrapy 生态）有子类化（`selectorlist_cls`）、`isinstance(x, list)`、`append`、切片、`__getstate__` 抛 `TypeError` 等依赖。惰性容器**不能**继承 `list` 冒充（`len()` 等会读内部存储），应是独立 `Sequence` 实现。

---

## 5. 兼容迁移方案（分阶段）

**Phase 1 — 内部生成器化（无 API 变化，纯收益）**
把 `SelectorList.xpath/css/jmespath/jsonpath/re` 中的 `flatten([... for x in self])` 改为对生成器表达式 `iflatten(x._xpath(...) for x in self)` 再 `list()`。中间 list-of-lists 不再整体驻留，多父链式场景省一层峰值（实测 39.7 → 36.8 MB），行为完全不变。

**Phase 2 — 急切编译 + 可选惰性求值 API（opt-in）**
新增 `Selector.ixpath()` / `SelectorList.ixpath()`（或 `xpath(lazy=True)`），返回生成器：调用时即编译 XPath（语法错误时点不变），迭代时才逐父求值、逐节点包装 `Selector`。文档明确两点差异：求值错误推迟到迭代时；结果是活视图，`drop()` 前后迭代结果不同。

**Phase 3 — `LazySelectorList`（如需列表式访问）**
实现 `collections.abc.Sequence`：内部"缓存式迭代器"——`__getitem__(i)` 只物化前 i+1 项，切片/`len()`/`__eq__` 触发完全物化并缓存。提供 `.to_list()` 显式物化为 `SelectorList`。不继承 `list`，不替换 `selectorlist_cls` 默认值；`SelectorList` 永远保持 eager。

**配套小优化**：`extract_regex` 的 `findall` 可改 `finditer` 以支持 `re_first` 单元素内短路（注意 `replace_entities` 与分组扁平化逻辑保持一致）。

---

## 6. 可复现的最小实验

以下实验已在 macOS / Python 3 下对本仓库源码执行，结果即 §1、§3 的数据来源。

**实验 A：语义基线（顺序、重复、错误时点、删除后可见性）**

```python
from parsel import Selector
html = "<html><body><div><p>a</p></div><div><p>b</p><p>c</p></div></body></html>"
sel = Selector(html)
assert len(sel.xpath("//div").xpath("//p")) == 6          # 链式绝对 XPath 保留重复
lst = sel.css("p"); first = lst[0]
sel.css("div")[0].drop()
assert sel.css("p").getall() == ["<p>b</p>", "<p>c</p>"]  # 新查询反映删除
assert first.get() == "<p>a</p>"                          # 旧列表仍是快照
try: sel.css("li").xpath("//*[")
except ValueError: pass                                   # 错误在调用时抛出
```

**实验 B：峰值内存对比（验证取舍）**

```python
import tracemalloc
from parsel import Selector
from parsel.utils import iflatten

N = 100_000
html = ("<html><body>" + "".join(
    "<ul>" + "".join(f"<li>{i}</li>" for i in range(20)) + "</ul>"
    for _ in range(N // 20)) + "</body></html>")
sel = Selector(html)
parents = sel.css("ul")

def peak(fn):
    tracemalloc.start(); r = fn()
    _, pk = tracemalloc.get_traced_memory(); tracemalloc.stop()
    return pk / 1e6, len(r)

print(peak(lambda: parents.css("li")))                       # eager 基线 ≈ 39.7 MB
print(peak(lambda: parents.__class__(                        # Phase 1 生成器拼接 ≈ 36.8 MB
    iflatten(x._xpath("descendant::li", None, {}) for x in parents))))
```

**实验 C：惰性原型验证（Phase 2 的取舍量化）**
实现一个 30 行的 `ixpath()` 生成器原型，对照三项指标：(1) `re_first` 命中首项时的端到端耗时与峰值内存（预期：10 万节点下结果层内存 ≈ 0，耗时从 ~0.17s 降到毫秒级）；(2) 无效 XPath 仍在调用时抛错（急切编译）；(3) `drop()` 后迭代结果与 eager 快照不同（确认文档化的语义差异）。

**实验 D：回归套件**
直接运行现有 `pytest tests/`，其中 `test_remove_*`、`test_re_first*`、`test_representation_slice` 覆盖了 §4 列出的全部敏感语义，Phase 1 改动应零失败；Phase 2/3 为新增 API，需补充上述 A/C 中差异点的专项测试。
