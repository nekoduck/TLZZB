{{ state }}

<你能做的事>
{% for a in actions %}
{{ a.id }}：{% if a.star %}★ {% endif %}{{ a.text }}
{% endfor %}
none：什么也不做
</你能做的事>

<你可以开口的方式>
{% if acts %}
{% for a in acts %}
{{ a.id }}：{{ a.label }}
{% endfor %}
说话的对象（to 里填 id，对众人就留空）：{% for p in people %}{{ p.id }}＝{{ p.name }}{% if not loop.last %}｜{% endif %}{% endfor %}

语气（stance）：{% for s in stances %}{{ s.id }}＝{{ s.label }}{% if not loop.last %}｜{% endif %}{% endfor %}

{% else %}
此刻没人听你说话，不开口（speech 填 null）。
{% endif %}
</你可以开口的方式>

<你可以说的事>
{% for f in facts %}
{{ f.id }}：{{ f.text }}
{% else %}
无
{% endfor %}
</你可以说的事>

<冲你来的请求>
{% for r in requests %}
{{ r.id }}：{{ r.text }}｜可选的答复：{{ r.replies }}
{% else %}
无
{% endfor %}
</冲你来的请求>
