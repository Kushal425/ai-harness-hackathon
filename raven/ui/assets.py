"""Raven's mascot art (visual identity). Kept separate from rendering
logic (raven/ui/animations.py) so the art can change without touching
how/when it's drawn.

The raven is Braille dot art (each cell packs a 2x4 dot grid), traced from
the reference image's silhouette rather than hand-drawn, so it stays small
yet still reads as a perched raven. RAVEN_BANNER sits beside the RAVEN_WORDMARK
on wide terminals; RAVEN_COMPACT is for narrow ones."""

RAVEN_BANNER = """\
 ⣀⣠⣴⢈⠹⢿⣤⡀
⠚⠛⠻⠏ ⣴⣿⣿⣷
   ⣰⣾⣿⣿⣿⣿⣷⣤⡀
   ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦
   ⢿⡇⠘⣿⣿⣿⣿⣿⣿⣿⣦⡀
   ⠘⢿⡄⠘⣿⣿⣿⣿⣿⣿⣿⣿⡄
    ⠘⢿⡆⠈⠙⠿⢿⣿⣿⣿⣿⣷⡄
      ⠻⣆⣀⣀ ⠙⠿⣿⣿⣿⣷⡀
        ⠉⣿⣿⡶⠄⢈⣿⣿⣿⣦⡀
      ⢀⣀⣾⣿⡏  ⠉⢻⣿⣿⣿⣿⡀
     ⢶⡿⠉⡟⢹⣿⣶⡄  ⢻⣿⣿⣿⡷
     ⠈   ⠘ ⠘⠻⠂  ⠙⢿⣿⣷"""

RAVEN_COMPACT = """\
⣠⣤⡆⠄⣻⣦
  ⣠⣴⣿⣿⣧⣀
  ⢿⠿⣿⣿⣿⣿⣷⡀
  ⠸⣆⠹⣿⣿⣿⣿⣿⣆
   ⠙⢦⠈⠛⠻⣿⣿⣿⣆
    ⠈⠑⣶⣦⠌⢹⣿⣿⣄
   ⢀⣠⢴⣿⣇ ⠈⢻⣿⣿⣅
   ⠘⠋⠈⠘⠉⠻⠄ ⠻⢿⣯"""

RAVEN_WORDMARK = """\
██████╗  █████╗ ██╗   ██╗███████╗███╗   ██╗
██╔══██╗██╔══██╗██║   ██║██╔════╝████╗  ██║
██████╔╝███████║██║   ██║█████╗  ██╔██╗ ██║
██╔══██╗██╔══██║╚██╗ ██╔╝██╔══╝  ██║╚██╗██║
██║  ██║██║  ██║ ╚████╔╝ ███████╗██║ ╚████║
╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═══╝"""
