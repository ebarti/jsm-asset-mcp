# Practical Assets recipes

These are MCP tool calls, shown as `{ "tool": ..., "arguments": ... }` so you can copy the `arguments` into an MCP client. **Every schema name, object type, attribute, ID, object key, and project below is fictional.** Replace each with values from your own workspace. Run the first five discovery calls before assuming an attribute exists. AQL uses your schema's names and data types; [Atlassian's syntax reference](https://support.atlassian.com/assets/docs/use-assets-query-language-aql/) defines its operators and functions.

## Discover your data model

1. Find schema IDs. This call merges every schema-list page:

```json
{"tool":"list_object_schemas","arguments":{}}
```

2. Inspect one schema from the returned `values`:

```json
{"tool":"get_object_schema","arguments":{"schema_id":"101"}}
```

3. Discover its object types and their IDs:

```json
{"tool":"list_object_types","arguments":{"schema_id":"101"}}
```

4. Get field names, types, and attribute IDs for a selected type:

```json
{"tool":"get_object_type_attributes","arguments":{"object_type_id":"201"}}
```

5. Read the cross-schema text summary when deciding which type to search. This direct call does not invoke a translation provider, but your MCP host may process its result:

```json
{"tool":"get_schema_summary","arguments":{}}
```

6. List the exact status names valid in that schema, global ones included, before filtering on `Status`:

```json
{"tool":"list_status_types","arguments":{"schema_id":"101"}}
```

7. List the reference type names, such as `Installed` or `Depends`, used by `refType` in reference functions:

```json
{"tool":"list_reference_types","arguments":{"schema_id":"101"}}
```

8. Check the size of the workspace: the total object count and the count per schema:

```json
{"tool":"get_usage","arguments":{}}
```

## Inventory and data quality

9. Start with one bounded page of active laptops. `max_results` is a page size when `fetch_all` is false:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Laptop\" AND Status = \"Active\" ORDER BY Name ASC","start_at":0,"max_results":25}}
```

10. Fetch the next page using the previous page's offset and returned count. Here 25 is an example offset; use the real response to avoid gaps:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Laptop\" AND Status = \"Active\" ORDER BY Name ASC","start_at":25,"max_results":25}}
```

11. Find laptops with no owner, then assign follow-up outside this server:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Laptop\" AND Owner IS EMPTY","max_results":50}}
```

12. Find names with a prefix using AQL's `STARTSWITH` operator:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Server\" AND Name STARTSWITH \"prod-\"","max_results":25}}
```

13. Retrieve all matching server objects only when you need the full set. This uses total-count plus paged AQL requests and can produce a large response:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Server\" AND Status = \"Active\"","max_results":100,"fetch_all":true}}
```

14. Ask for an exact laptop count in natural language. `search_assets` requires a configured provider extra and sends the schema summary and question to it; count results have no object `values`:

```json
{"tool":"search_assets","arguments":{"question":"How many active Laptop objects are in Assets?"}}
```

15. Ask for a short natural-language list, then inspect `_generated_aql` before using the result:

```json
{"tool":"search_assets","arguments":{"question":"Show the first 10 active laptops","max_results":25}}
```

## Lifecycle, linked work, and impact

16. Find fictional software licenses expiring before month end; replace `Expiry Date` with a real date attribute:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Software License\" AND \"Expiry Date\" < endOfMonth()","max_results":50}}
```

17. Find servers whose `Updated` timestamp is older than 90 days. This flags review candidates; it does not prove the machines are unused:

```json
{"tool":"execute_aql","arguments":{"query":"objectType = \"Server\" AND Updated < \"now(-90d)\"","max_results":50}}
```

18. Use a JQL filter inside AQL to find objects linked to open work in a fictional `OPS` project; replace the project key. Check the returned tickets before treating an object as incident-affected:

```json
{"tool":"execute_aql","arguments":{"query":"object HAVING connectedTickets(project = OPS AND statusCategory != Done)","max_results":25}}
```

19. Explore objects that reference a fictional database object. First obtain the real database key; the reference direction depends on your schema:

```json
{"tool":"execute_aql","arguments":{"query":"object HAVING outboundReferences(Key = \"LAB-17\")","max_results":25}}
```

20. Before changing or retiring an object, count what references it, grouped by object type and reference type. It returns counts, not objects; use the AQL above to list them:

```json
{"tool":"get_object_reference_info","arguments":{"object_id":"401"}}
```

21. Retrieve one returned object using its `id`, rather than its display key:

```json
{"tool":"get_object","arguments":{"object_id":"401"}}
```

22. Inspect the object's full attribute values to confirm ownership or relationships:

```json
{"tool":"get_object_attributes","arguments":{"object_id":"401"}}
```

23. Review its change history for audit context:

```json
{"tool":"get_object_history","arguments":{"object_id":"401"}}
```

24. Read the Jira tickets connected to that object:

```json
{"tool":"get_connected_tickets","arguments":{"object_id":"401"}}
```

25. Ask for every expiring license through the provider when you do not know AQL. The explicit `fetch_all` tool argument requests all **object** pages after translation; inspect the generated AQL and be prepared for a large result:

```json
{"tool":"search_assets","arguments":{"question":"List all Software License objects whose Expiry Date is before the end of this month","max_results":100,"fetch_all":true}}
```

To rank owners by license count, call `execute_aql` for the relevant licenses, retrieve or inspect each object's owner values, and group the results in your MCP host or a separate script. The server does not expose grouping, ranking, CSV export, a scheduler, or an automation agent. Similarly, an impact assessment is a sequence of reference queries and ticket/object reads; it is not a single built-in graph traversal result.

## Monitor imports

These calls only read import configuration and results; none starts or changes an import. The IDs are fictional.

26. List a schema's import sources to find an import source ID and see which ones run on a schedule. This uses an undocumented endpoint (see the [tool reference](tools.md)):

```json
{"tool":"list_import_sources","arguments":{"schema_id":"12"}}
```

27. Read one source's definition and schedule; its import-specific configuration is reduced to key names:

```json
{"tool":"get_import_source","arguments":{"import_source_id":"4f6c2d1e-8a3b-4c5d-9e7f-1a2b3c4d5e6f"}}
```

28. Check whether that source is idle, running, missing its mapping, or disabled:

```json
{"tool":"get_import_config_status","arguments":{"import_source_id":"4f6c2d1e-8a3b-4c5d-9e7f-1a2b3c4d5e6f"}}
```

29. See how its last run went: entries read, objects created, updated, identical, and errors per object type:

```json
{"tool":"get_last_import_execution","arguments":{"import_source_id":"4f6c2d1e-8a3b-4c5d-9e7f-1a2b3c4d5e6f"}}
```

30. Re-read a specific run using the `executionId` from the previous response:

```json
{"tool":"get_import_execution_status","arguments":{"import_source_id":"4f6c2d1e-8a3b-4c5d-9e7f-1a2b3c4d5e6f","execution_id":"<executionId returned by get_last_import_execution>"}}
```

31. Follow an import in progress, or see who ran the latest one and whether it was manual or scheduled. Pass the import source ID, not an execution ID:

```json
{"tool":"get_import_progress","arguments":{"import_source_id":"4f6c2d1e-8a3b-4c5d-9e7f-1a2b3c4d5e6f"}}
```

## Controlled write example

**The next calls change Jira data.** Use a disposable object type and a test workspace with write permission. `JSM_READ_ONLY=true` leaves these write tools unregistered, and `JSM_WRITE_SCHEMA_IDS` can restrict their target schemas; the server does not ask for confirmation before a write. Discover a real object type ID and its required attribute IDs first. If your host or token is intended to be read-only, do not run this section.

32. Create a disposable object. Replace fictional type/attribute IDs and values with fields accepted by your type:

```json
{"tool":"create_object","arguments":{"object_type_id":"201","attributes":[{"objectTypeAttributeId":"301","objectAttributeValues":[{"value":"Disposable test laptop"}]}]}}
```

33. Copy the `id` **returned by this create** into the next call and verify what Jira stored:

```json
{"tool":"get_object","arguments":{"object_id":"<id returned by create_object>"}}
```

34. Update only that disposable object, using the same returned object ID and its type ID:

```json
{"tool":"update_object","arguments":{"object_id":"<id returned by create_object>","object_type_id":"201","attributes":[{"objectTypeAttributeId":"301","objectAttributeValues":[{"value":"Disposable test laptop updated"}]}]}}
```

35. Read its attributes to verify the update:

```json
{"tool":"get_object_attributes","arguments":{"object_id":"<id returned by create_object>"}}
```

36. Delete **only** that disposable returned ID after verification:

```json
{"tool":"delete_object","arguments":{"object_id":"<id returned by create_object>"}}
```

For a read-only workflow, use a host tool allowlist that excludes `create_object`, `update_object`, and `delete_object`, plus Jira permissions that cannot write. The [stdio example](examples/stdio_client.py) has only offline listing and two explicit read-only call paths.
