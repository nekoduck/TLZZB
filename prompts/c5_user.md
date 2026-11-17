【出了什么事】{{ what }}

【他此刻的局面】
{{ situation }}

【候选目标】
{% for g in goals %}
{{ g.id }}：{{ g.text }}
{% for s in g.steps %}
  - {{ s.id }}：{{ s.text }}
{% endfor %}
{% endfor %}

请选一个目标的 id，再从它下面挑出至多三步，按先后排好。
