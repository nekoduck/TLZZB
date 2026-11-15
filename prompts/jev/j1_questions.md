# J1 输入归类的固定问法（方案 7.3，D-013 中文问法，D-014 是非题另问）。
# 每行「键：文字」。键里带点的是某道 choice 题的选项说明；{事} 由代码换成候选的一句话。
# 以 # 开头的行是注释。改这里不用改代码。

kind：「玩家的话」这一节主要是在做什么？
kind.act：做一个身体动作
kind.say：开口说话
kind.both：既做一个身体动作，又开口说话
kind.meta：在问这个游戏本身怎么操作、怎么存档

has_speech：「玩家的话」里有说出口的话。
meaningful：「玩家的话」读得出意思。

verb：「玩家的话」里要做的那个身体动作是哪一种？
verb.none：都不是，或者没有身体动作
verb.go：走到别处去
verb.follow：跟着某人走
verb.flee：拔腿逃跑
verb.hide：躲起来、藏起来
verb.look：四下看看
verb.listen：侧耳细听、偷听
verb.examine：仔细看某个人、某样东西
verb.search：翻找、搜一搜
verb.take：拿起、捡起地上的东西
verb.drop：放下、扔掉身上的东西
verb.give：把东西交给别人
verb.show：把东西亮给别人看
verb.use：吃、喝、服药、用某样东西
verb.steal：偷别人身上的东西
verb.help：搀扶、包扎、照料别人
verb.restrain：把人捆起来、制住
verb.release：给人松绑、放人
verb.attack：动手打人、杀人
verb.block：挡在某人面前、拦住
verb.push：推、撞
verb.study：读书、跟人学本事
verb.practice：练功、练手艺
verb.teach：教别人本事
verb.wait：等一等、什么也不做
verb.rest：歇息、睡觉
verb.struggle：挣扎、挣脱
verb.setpiece：{事}

target：这个动作冲着谁、对着什么？
target.none：没有对象
target2：这个动作牵涉到的第二个人或第二样东西是什么？
target2.none：没有

act：「玩家的话」里说出口的那句话，是哪一种言语行为？
act.greet：打招呼、自报家门
act.tell：告诉对方一件事
act.ask：询问，想从对方那里得到答案
act.request：请求对方去做一件事
act.agree：答应对方的请求或条件
act.refuse：拒绝对方的请求或条件
act.bargain：对对方的请求开条件
act.promise：许诺自己会做一件事
act.threaten：威胁对方
act.plead：求饶、求情
act.order：命令手下去做一件事
act.warn：提醒对方有危险
act.comfort：安慰对方
act.insult：辱骂、讥讽对方
act.taunt：出言相激
act.lie：明知不实，故意编话骗人

to：那句话是对谁说的？
to.all：对在场的众人

ask：那句话是在求对方{事}。
claim：那句话断言了这件事：{事}
self：那句话声称自己做过这件事：{事}
self_unlisted：那句话声称自己做过某件事，可这件事不在上面列出的事情里。

cls：所求的那件事，对被求的人来说算哪一类？
cls.trivial：举手之劳
cls.effort：费事
cls.costly：有损己利
cls.risky：冒险
cls.betray：违背所属
urgent：所求的那件事正好帮到被求的人眼下的急需。

wish：这个心愿与这件事物有关：{事}
