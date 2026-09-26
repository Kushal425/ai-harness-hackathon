"""Raven's ASCII mascot art (plan-approved visual identity). Two sizes:
RAVEN_BANNER for the startup sequence (skipped for narrow terminals or
non-interactive/piped runs), RAVEN_COMPACT for tight spaces. Kept separate
from rendering logic (raven/ui/render.py, raven/ui/animations.py) so the
art can change without touching how/when it's drawn."""

RAVEN_BANNER = r"""
                     .:=+*##*+=:.
                 .:+#%%%%%%%%%%%#+:.
              .:+#%%%%%%%%%%%%%%%%%#+:.
            :+#%%%%%%%(o)%%%%%%%%%%%%#:
   <========#%%%%%%%%%%%%%%%%%%%%%%%%#:
            :+#%%%%%%%%%%%%%%%%%%%%%#+:.
              ':+#%%%%%%%%%%%%%%%%%#+:.  '-.
                 ':+#%%%%%%%%%%%%%#+:.      '-.
                    ':+#%%%%%%%%%#+:.           '-.
                       ':+#%%%%%#+:.                '-.
                          ':+#%%#+:.                    '-.
                             ':+#+:.                        '-.
                                ':.                              '.
                              /  |  \
                             /   |   \
                            /    |    \
                           '     |     '
                        ___|_____|_____|___
"""

RAVEN_COMPACT = r"""
        .:+*#+:.
      :+#%%(o)%%#:
 <===#%%%%%%%%%%%#
      ':+#%%%%#+:'
         ':+#+:'
          /   \
         '     '
"""
