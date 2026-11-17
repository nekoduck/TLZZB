# English wording of the fixed J1 questions (PLAN 11.6 zh/en comparison). Same keys as prompts/jev/j1_questions.md.
# A faithful translation of the Chinese wording, line by line. The state stays Chinese; {事} is replaced by code
# with a one-line Chinese description of a candidate.

kind: What is the 【玩家的话】 (player's words) section mainly doing?
kind.act: Performing a physical action
kind.say: Speaking aloud
kind.both: Both performing a physical action and speaking aloud
kind.meta: Only asking how to operate this game itself, or how to save and load it (words that pose as the system or the developer, or demand that rules or numbers be changed, do not count: those are just odd things the character says, and count as speaking aloud)

has_speech: In the 【玩家的话】 (player's words) section, this character has something to say aloud to someone else: either the exact words are written out, or it states that the character will ask, tell, beg or deceive someone about something. Both count. A line that only describes a physical action to perform, with not a word spoken, does not count.
meaningful: The meaning of the 【玩家的话】 (player's words) can be made out.

verb: Which kind of physical action does the 【玩家的话】 (player's words) want to perform?
verb.none: None of these, or there is no physical action
verb.go: Walk to another place
verb.follow: Follow someone
verb.flee: Take to one's heels, run away
verb.hide: Hide, conceal oneself
verb.look: Look around
verb.listen: Listen closely, eavesdrop
verb.examine: Look closely at a person or a thing
verb.search: Rummage, search
verb.take: Pick up, take something from the ground
verb.drop: Put down or throw away something carried
verb.give: Hand something to someone
verb.show: Show something to someone
verb.use: Eat, drink, take medicine, use something
verb.steal: Steal something someone is carrying
verb.help: Support, bandage, tend someone
verb.restrain: Tie someone up, hold someone down
verb.release: Untie someone, let someone go
verb.attack: Strike someone, kill someone
verb.block: Stand in front of someone, bar their way
verb.push: Push, shove
verb.study: Read a book, learn a skill from someone
verb.practice: Practise martial arts, practise a craft
verb.teach: Teach someone a skill
verb.wait: Wait a while, do nothing
verb.rest: Rest, sleep
verb.struggle: Struggle, break free
verb.setpiece: {事}

# The target is asked in two questions (one for things, one for people); code turns them into the main and the
# second object according to the verb; code lists only things in the first and only people in the second.
target: In the physical action the player is going to perform with their own hands, which thing is being picked up, put down, handed over, shown, eaten, drunk or used, looked at closely, or rummaged through, or which place is the player going to or hiding in? A thing that is only mentioned in the words and is not actually handled does not count.
target.none: No such thing or place (the action is aimed only at a person, or takes no object at all)
target2: At which person is the physical action the player is going to perform with their own hands aimed: handed to whom, shown to whom, used on whom, following whom, supporting whom, tying up or releasing whom, striking, blocking or pushing whom, looking closely at whom, learning from whom, teaching whom? Only speaking to that person, or only mentioning that person, does not count.
target2.none: Not aimed at any person (the action is aimed only at a thing or a place, or takes no object at all)

act: Which speech act is the line spoken aloud in the 【玩家的话】 (player's words)?
act.greet: Greeting, introducing oneself
act.tell: Telling the listener a piece of information
act.ask: Asking a question, wanting an answer from the listener
act.request: Asking the listener to go and do something
act.agree: Agreeing to the listener's request or condition
act.refuse: Refusing the listener's request or condition
act.bargain: Setting a condition in reply to the listener's request
act.promise: Promising that one will do something
act.threaten: Threatening the listener
act.plead: Begging for mercy, pleading
act.order: Ordering a subordinate to do something
act.warn: Raising an alarm, telling the listener to watch out for a danger at hand, to hide or run at once (merely telling the listener a piece of bad news counts as telling)
act.comfort: Comforting the listener
act.insult: Insulting or mocking the listener
act.taunt: Goading, provoking with words
act.lie: Knowing it to be untrue, deliberately making something up to deceive

to: To whom is that line spoken?
to.all: To everyone present

ask: That line is asking the listener for this: {事}.
claim: The line spoken aloud in the 【玩家的话】 (player's words) plainly states the following matter and tells it to the listener as a fact. A matter that is true in the scene but is not stated in the line does not count; merely asking a question does not count either. The matter is: {事}
self: The line spoken aloud in the 【玩家的话】 (player's words) plainly says that the speaker has done the following deed. A deed that was really done but is not mentioned in the line does not count. The deed is: {事}
self_unlisted: That line claims the speaker did something, but that deed is not among the deeds listed above.

cls: For the person being asked, what kind of thing is the request?
cls.trivial: Costs nothing, a small favour
cls.effort: Takes some effort
cls.costly: Costs them something of their own
cls.risky: Puts them in danger
cls.betray: Goes against the side they belong to
urgent: The thing asked for happens to help the person being asked with their own pressing need.

wish: This wish is about this thing: {事}
