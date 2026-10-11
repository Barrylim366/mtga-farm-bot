# Forge advisor

The Java side of the Forge Engine (see *Forge Engine (experimental)* in the main
README). It is a small program that loads an Arena position into
[Forge](https://github.com/Card-Forge/forge) and answers with Forge's decision. It
reads one JSON request per line on stdin and writes one JSON reply per line on
stdout. `ForgeEngine/client.py` starts it and talks to it.

The source here was taken from
[mtga-play-bot](https://github.com/Funny-Bones/mtga-play-bot) (GPL-3.0). It links
against Forge, so it is GPL-3.0 as well; see `LICENSE` in the repository root.

It is not built when the bot starts. `tools/build_forge_package.py` compiles it
against a Forge build and puts it, with Forge and a Java runtime, into the
package that ticking "Enable Forge Engine" downloads.
