<场景>{{ scene }}</场景>
<称呼表>{{ names }}</称呼表>
<可选动作>
{% for o in options %}
{{ o.id }}｜{{ o.fact }}｜方向：{{ o.tag }}
{% endfor %}
</可选动作>
{% if avoid %}
<上回合选过>{{ avoid }}</上回合选过>
{% endif %}
按原来的顺序给出 options，每项的 id 照抄上面的 id。
