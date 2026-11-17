# J2b 的问题（方案 7.3）。YAML；{name} 由代码填。只在要开口时发出，状态末尾加一行【你刚决定】。
decided: 【你刚决定】{action}
act: 你要怎么开口？
to: 这句话冲谁说？
all: 对众人
stance: 你说话的语气是哪一种？
tell: 你会说出这件事：{fact}
reply: '{request}。你怎么答复？'
cond: 对方要答应哪个条件，你才肯照办？
whisper: 你会压低声音，只让对方听见。
acts:
  greet: 打个招呼，报上名号
  tell: 告诉对方一件事
  ask: 问对方话
  request: 求对方做一件事
  agree: 答应
  refuse: 拒绝
  bargain: 还价，开个条件
  promise: 许下一个诺言
  threaten: 威胁对方
  plead: 求饶、求情
  order: 下令
  warn: 提醒对方当心
  comfort: 宽慰对方
  insult: 辱骂对方
  taunt: 出言相激
  lie: 编话搪塞
stances:
  plain: 平常
  warm: 热络
  cold: 冷淡
  angry: 恼怒
  pleading: 恳求
  mocking: 讥讽
  evasive: 支吾
replies:
  agree: 答应
  refuse: 拒绝
  bargain: 还价，开个条件
  tell: 照实回答
  lie: 编话搪塞
