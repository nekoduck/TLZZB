【此刻】{{ clock }}
【你近来做的事】
{% for line in recent %}
- {{ line }}
{% else %}
- 没做什么
{% endfor %}
【未了的牵挂】
{% for t in threads %}
- {{ t }}
{% else %}
- 无
{% endfor %}
【张力】{{ tension }}（满分一百）；已经有{{ quiet }}回合没有新的动静。
