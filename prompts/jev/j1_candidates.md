{# C1 细读输入的候选块（方案 7.5：输入与 J1 的状态和候选相同）。编号照抄，不自造。 #}
【动词】（verb 填编号；没有身体动作填 null）
{% for o in verbs %}
{{ o.key }}：{{ o.desc }}
{% endfor %}
【场景里有的东西】（target、target2 填编号）
{% for o in objects %}
{{ o.key }}：{{ o.desc }}
{% endfor %}
【言语行为】（act 填编号；没说话填 null）
{% for o in acts %}
{{ o.key }}：{{ o.desc }}
{% endfor %}
【说话的对象】（to 填编号的列表；对众人填空表）
{% for o in people %}
{{ o.key }}：{{ o.desc }}
{% endfor %}
【对方做得到的事】（asks 填编号；cls 填 trivial、effort、costly、risky、betray 之一）
{% for o in asks %}
{{ o.key }}：{{ o.desc }}
{% else %}
（无）
{% endfor %}
【可能相关的事】（claim_refs 填编号）
{% for o in claims %}
{{ o.key }}：{{ o.desc }}
{% else %}
（无）
{% endfor %}
【你近来做过的事】（声称自己做过的事填编号；说的是自己做过的事而这里没有，填 self_unlisted）
{% for o in selfs %}
{{ o.key }}：{{ o.desc }}
{% else %}
（无）
{% endfor %}
