"""
Checks done at the input and outputs received.
These are basic checks that tests the structure of the output, rather
than semantic closeness.
"""
def valid_output(input:dict, output:dict):
    if not input.get("selected_text"):
        return {"key" : "valid_output", "score":"0"}
    both_attrs_present = output.keys() and ["meaning", "fit"]
    print(both_attrs_present)
    return {"key" : "valid_output", "score":"0", "id":input.get("id")}