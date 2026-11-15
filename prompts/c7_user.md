<背景>{{ background }}</背景>
<收场>{{ clock }}，{{ place }}。{{ ended_text }}</收场>
<你亲眼见过的大事>
{% for line in seen %}
- {{ line }}
{% else %}
- 没有什么大事
{% endfor %}
</你亲眼见过的大事>
<你听说的事>
{% for line in heard %}
- {{ line }}
{% else %}
- 无
{% endfor %}
</你听说的事>
<收场时你所知的各人下落>
{% for line in whereabouts %}
- {{ line }}
{% else %}
- 无
{% endfor %}
</收场时你所知的各人下落>
<了结了的牵挂>
{% for line in closed_threads %}
- {{ line }}
{% else %}
- 无
{% endfor %}
</了结了的牵挂>
<没了结的牵挂>
{% for line in open_threads %}
- {{ line }}
{% else %}
- 无
{% endfor %}
</没了结的牵挂>
<你的长进>
{% for line in growth %}
- {{ line }}
{% else %}
- 没有什么长进
{% endfor %}
</你的长进>
请写这一段后事。
