"""Package marker for the repository's pytest helpers.

OpenMHS has a separate integration tree that also contains a ``tests``
package.  Keeping this directory a regular package makes imports such as
``tests._ports`` resolve to the repository helpers even after the optional
OpenMHS bootstrap adds its source paths.
"""
