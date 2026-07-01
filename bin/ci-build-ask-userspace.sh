# Diagnostic 5: CT-TRACE at pthread_create — proves thread spawns
old5 = '	if (pthread_create(&ctx->pthread, NULL, cmmCtThread, ctx) < 0)'
new5 = '''	cmm_print(DEBUG_CRIT, "CT-TRACE: cmmCtInit: spawning cmmCtThread\\n");
	if (pthread_create(&ctx->pthread, NULL, cmmCtThread, ctx) < 0)'''
if old5 in s:
    s = s.replace(old5, new5)
    print('Injected CT-TRACE pthread_create')
else:
    print('WARNING: pthread_create pattern not found')

# Diagnostic 6: CT-TRACE at TOP of cmmCtThread — proves thread started
old6 = '''static void *cmmCtThread(void *data)
{
	struct cmm_ct *ctx = data;'''
new6 = '''static void *cmmCtThread(void *data)
{
	struct cmm_ct *ctx = data;

	cmm_print(DEBUG_CRIT, "CT-TRACE: cmmCtThread started, pid=%d\\n", getpid());'''
if old6 in s:
    s = s.replace(old6, new6)
    print('Injected CT-TRACE cmmCtThread started')
else:
    print('WARNING: cmmCtThread pattern not found')