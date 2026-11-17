<此刻>{{ now }}</此刻>

<新近的经历>
{% for m in memories %}
{{ loop.index }}. {{ m }}
{% endfor %}
</新近的经历>

<从前的想法>
{% for r in reflections %}
- {{ r }}
{% else %}
无
{% endfor %}
</从前的想法>

<相关的人与物>
{% for p in people %}
{{ p.id }}：{{ p.name }}{% if p.rel %}（{{ p.rel }}）{% endif %}

{% else %}
无
{% endfor %}
</相关的人与物>
