/* This executable exists solely to prove that every original host TU links.
 * Upstream constructors remain present: never execute this artifact. */
#include <stdio.h>

int main(void)
{
    fputs("LINK-ONLY-DO-NOT-RUN: original 1.9 metadata compile baseline; no runtime validation.\n", stderr);
    return 78;
}
