Our accountant noticed that some invoice totals are one cent off: the total printed on the invoice is not the sum of its lines. She also says that the same invoice sometimes gets different amounts depending on whether we import it from the CSV or from the JSON file.

Please find and fix the cause. The rule we agreed with her: amounts stay exact decimals, never floating point; every line is rounded half up to the cent (its net, and the tax on that net), and an invoice's totals are the sums of its rounded lines.
