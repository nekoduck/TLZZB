## 任务：P3 开局与走向
{% if task == 'opening' %}

这一条请求只填 `opening_yaml`、`baseline_yaml`、`checks`、`diff_report`，`agendas` 与 `situations` 留空表。

1. **opening_yaml**：开局那一刻（第一日辰时，剑湖宫比剑正在进行）的世界快照，按 opening.yaml 的格式写全文。以下面现有的 opening.yaml 为底，逐项对照原文改正：谁在哪、带着什么身份与关系、玩家开局知道什么。原著里此后才发生的事不进快照；图外的人写 `onstage: false`，由他们的打算入场。
2. **baseline_yaml**：原著参照点的全文（格式见 system）。按原文排出开局之后各件要事，`by` 按原文里的时间线索估计；拿不准的不写 by。以现有的 baseline.yaml 为底改正。
3. **checks**：下面八条逐条核对原文。每条一项：`item` 以序号开头（如「1. ……」），`paragraphs` 写依据的段落编号，`conclusion` 用自己的话写结论；原文没写的明说「原文未写」，给出可依据的最近段落。
{% for c in checks %}
   - {{ c }}
{% endfor %}
4. **diff_report**：Markdown。逐条列出现有种子内容（开局快照、打算、情境、参照点、人物与地点的设定）与原文不符之处：现在怎么写的、原文是怎样的（段落编号）、建议怎么改。程序另会逐项比对文件，这里写你的理由与依据。

现有的 opening.yaml：

```yaml
{{ opening_yaml }}
```

现有的 baseline.yaml：

```yaml
{{ baseline_yaml }}
```
{% elif task == 'agendas' %}

这一条请求只填 `agendas`，其余字段留空。下面每个人一份：`id` 是人物 id，`yaml` 是 agendas/<id>.yaml 的全文。

打算是此人开局时心里的计划，加上他将要撞上的机缘，也就是「没人打扰时他大概会怎么走」。以现有的为底，对照原文改正每一步的先后、地点、时间（start、deadline、takes）、做法（do），并且：

- 每一步都写 `pre`：这一步真正靠什么成立（人还活着、东西还在、他知道那件事、对方还在那里……）。前提一旦不成立，这一步作废，他会重新打算。
- 坠崖、撞见这类不由他自己决定的事，不写成他的步骤，交给情境。
- 只写原文里有依据的步骤；原文没交代确切时间的，按前后的事估一个大约值。

开局快照（供对照谁在哪）：

```yaml
{{ opening_yaml }}
```

{% for aid, text in seeds.items() %}
### 现有的 agendas/{{ aid }}.yaml

```yaml
{{ text }}
```

{% endfor %}
{% elif task == 'agendas_new' %}

这一条请求只填 `agendas`，其余字段留空。

下面这些人在原文里出现过，但还没有打算。只给开局那一刻在场（或此后数日内会入场）、而且原文写了他们自己要做什么的人写一份 agendas/<id>.yaml；其余的不写。格式与要求同现有的打算：每一步都写 `pre`。

候选：
{% for x in ids %}
- {{ x }}
{% endfor %}

开局快照：

```yaml
{{ opening_yaml }}
```
{% elif task == 'situations' %}

这一条请求只填 `situations`，其余字段留空。下面每个情境一份：`id` 是情境 id，`yaml` 是 situations/<id>.yaml 的全文。

情境是条件一成立就会开场的一段事，不属于任何一个人的计划。以现有的为底，对照原文改正，并且：

- `when` 只写开场条件，不看先后；条件写全，免得前提没了它还照旧发生。
- 原著开局自带的写 `origin: canon`；通用的写 `generic`。
- `offscreen` 是玩家不在场时的结算，可以带 `check`，不是定数。
- `paragraphs` 写对应的原文段落编号。
- 没有「超时就照原著执行」这回事。

{% for sid, text in seeds.items() %}
### 现有的 situations/{{ sid }}.yaml

```yaml
{{ text }}
```

{% endfor %}
{% elif task == 'situations_new' %}

这一条请求只填 `situations`，其余字段留空。

已有的情境：{{ ids | join('、') }}。

对照原文第一至六回，找出开局之后原著里会撞上、却还没有写成情境的事（撞见、比试、被擒、盘问、救人之类），每件写一份 situations/<id>.yaml（`origin: canon`，写全 `when` 与 `paragraphs`）。已有的不要重写；找不到就返回空表。
{% endif %}

## 参考：P2 时间线（段落范围、人物、地点、经过）

{% for line in timeline %}
- {{ line }}
{% else %}
（P2 还没有结果）
{% endfor %}

## 参考：P1 实体（id｜类别｜名字）

{% for e in entities %}
- {{ e }}
{% else %}
（P1 还没有结果）
{% endfor %}
