`slugify` in the `textkit` package produces ugly slugs. `slugify("  Hello,  World! ")` returns `"--hello---world--"`, but it should return `"hello-world"`.

Fix it: any run of characters that are not ASCII letters or digits must become a single separator, and the slug must not start or end with a separator. This must also hold for a custom `sep`. Keep the existing behaviour for accents.
