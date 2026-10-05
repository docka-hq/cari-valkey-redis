# wave-1 - how the agents got there (90 graded attempts)

Tool outputs are not stored in wave-1 records; 'trouble words' counts the agent's own mentions of errors.

## cache

| | valkey | redis |
|---|---|---|
| attempts | 15 | 15 |
| median tool_calls | 7 | 8 |
| median tokens | 22029 | 34888 |
| median trouble_mentions | 0 | 1 |
| median doc_fetch_count | 0 | 0 |
| client_redis_py | 4/15 | 15/15 |
| client_valkey_py | 15/15 | 0/15 |
| client_glide | 2/15 | 0/15 |
| module_list | 0/15 | 2/15 |
| ttl_set | 15/15 | 15/15 |
| delete_on_update | 15/15 | 15/15 |
| docs read (attempts per domain) | {'valkey.io': 1} | {'redis.io': 2, 'readthedocs.io': 1} |

## vector

| | valkey | redis |
|---|---|---|
| attempts | 15 | 15 |
| median tool_calls | 18 | 17 |
| median tokens | 89567 | 54837 |
| median trouble_mentions | 1 | 1 |
| median doc_fetch_count | 2 | 2 |
| client_redis_py | 7/15 | 15/15 |
| client_valkey_py | 11/15 | 0/15 |
| ft_create | 15/15 | 12/15 |
| ft_search | 15/15 | 12/15 |
| vector_sets | 2/15 | 4/15 |
| hnsw | 3/15 | 3/15 |
| flat_index | 15/15 | 12/15 |
| on_json | 2/15 | 0/15 |
| resp2_forced | 0/15 | 3/15 |
| module_list | 15/15 | 15/15 |
| rdb_file | 4/15 | 6/15 |
| delete_on_update | 6/15 | 6/15 |
| docs read (attempts per domain) | {'valkey.io': 10, 'github.com': 2, 'githubusercontent.com': 2} | {'redis.io': 10, 'githubusercontent.com': 1} |

## migrate

| | valkey | redis |
|---|---|---|
| attempts | 15 | 15 |
| median tool_calls | 18 | 21 |
| median tokens | 146817 | 108918 |
| median trouble_mentions | 0 | 0 |
| median doc_fetch_count | 0 | 0 |
| client_redis_py | 11/15 | 11/15 |
| client_valkey_py | 1/15 | 0/15 |
| resp2_forced | 1/15 | 0/15 |
| module_list | 9/15 | 12/15 |
| replicaof | 15/15 | 15/15 |
| dump_restore | 2/15 | 1/15 |
| rdb_file | 8/15 | 5/15 |
| uses_old_cli | 9/15 | 6/15 |
| ttl_set | 3/15 | 1/15 |
| delete_on_update | 7/15 | 7/15 |
| docs read (attempts per domain) | {'valkey.io': 6, 'githubusercontent.com': 2, 'github.com': 1} | {'redis.io': 2, 'githubusercontent.com': 2, 'github.com': 1} |

## median tool calls per model and product

| model | valkey | redis |
|---|---|---|
| claude-opus-5-5 | 7 | 8 |
| openai/gpt-6-sol | 11 | 9 |
| deepseek/deepseek-v4.1-flash | 25 | 26 |
| z-ai/glm-5.3-flash | 31 | 24 |
| xiaomi/mimo-v2.6-flash | 17 | 17 |
