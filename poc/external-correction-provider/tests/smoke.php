<?php

/**
 * Smoke test for the external correction provider PoC.
 *
 * Runs the real AppProvider code paths against reflection-generated stubs
 * of the hosting system interfaces (no ILIAS, no database):
 *
 *   php smoke.php auth               - provider key check and GET /provider/data
 *   php smoke.php changes < body.json - PUT /provider/changes with a recording
 *                                       bridge; prints the applied changes as
 *                                       JSON (used by run_smoke.py)
 *
 * Requires: composer install (slim/psr7), PHP >= 8.2.
 */

declare(strict_types=1);

use Edutiek\AssessmentService\Assessment\Api\ComponentApiFactory;
use Edutiek\AssessmentService\Assessment\Apps\AppCorrectorBridge;
use Edutiek\AssessmentService\Assessment\Apps\AppProvider;
use Edutiek\AssessmentService\Assessment\Apps\RestContext;
use Edutiek\AssessmentService\Assessment\Apps\RestException;
use Edutiek\AssessmentService\Assessment\Apps\RestHelper;
use Edutiek\AssessmentService\Assessment\Authentication\FullService as AuthFullService;
use Edutiek\AssessmentService\Assessment\Data\Repositories;
use Edutiek\AssessmentService\Assessment\Data\Token;
use Edutiek\AssessmentService\Assessment\Permissions\ReadService as Permissions;
use Edutiek\AssessmentService\System\Config\ReadService as ConfigReadService;
use Edutiek\AssessmentService\System\Data\Config;
use Edutiek\AssessmentService\System\Data\UserData;
use Edutiek\AssessmentService\System\File\Delivery;
use Edutiek\AssessmentService\System\User\ReadService as UserReadService;
use Psr\Http\Message\ServerRequestInterface;
use Slim\Factory\AppFactory;
use Slim\Psr7\Factory\ServerRequestFactory;
use Slim\Psr7\Response;

require __DIR__ . '/../../../vendor/autoload.php';

/**
 * Build an instance of an interface or abstract class where every method
 * returns a type-appropriate default, optionally overridden per method with
 * a fixed value or a closure.
 */
function stub(string $class, array $overrides = []): object
{
    static $counter = 0;
    $ref = new ReflectionClass($class);

    $typeToString = function (?ReflectionType $type) use (&$typeToString): string {
        if ($type === null) {
            return '';
        }
        if ($type instanceof ReflectionNamedType) {
            $name = $type->getName();
            $str = ($type->isBuiltin() || in_array($name, ['self', 'static', 'parent']))
                ? $name : '\\' . $name;
            return ($type->allowsNull() && $name !== 'null' && $name !== 'mixed') ? '?' . $str : $str;
        }
        $sep = $type instanceof ReflectionIntersectionType ? '&' : '|';
        return implode($sep, array_map($typeToString, $type->getTypes()));
    };

    $methods = [];
    foreach ($ref->getMethods(ReflectionMethod::IS_ABSTRACT | ReflectionMethod::IS_PUBLIC) as $m) {
        if (!$m->isAbstract()) {
            continue;
        }
        $params = [];
        foreach ($m->getParameters() as $p) {
            $part = $typeToString($p->getType());
            $part .= ($part ? ' ' : '') . ($p->isVariadic() ? '...' : '') . '$' . $p->getName();
            if ($p->isDefaultValueAvailable()) {
                $part .= ' = ' . var_export($p->getDefaultValue(), true);
            }
            $params[] = $part;
        }
        $return = $typeToString($m->getReturnType());
        $returnDecl = $return !== '' ? ': ' . $return : '';

        $rt = $m->getReturnType();
        $rtName = $rt instanceof ReflectionNamedType ? $rt->getName() : null;

        if ($rtName === 'never') {
            $body = "throw new \\RuntimeException('stub: {$m->getName()} (never)');";
        } elseif ($rtName === 'void' || $rt === null) {
            $body = "\$this->__call_override('{$m->getName()}', func_get_args()); return;";
        } else {
            // reflection resolves 'self' to the declaring class name,
            // so treat any return type the stub satisfies as fluent
            $fluent = $rtName !== null && !$rt->isBuiltin()
                && is_a($ref->getName(), $rtName, true);
            $default = match (true) {
                $rt instanceof ReflectionNamedType && $rt->allowsNull() => 'null',
                $rtName === 'self', $rtName === 'static', $fluent => '$this',
                $rtName === 'bool' => 'false',
                $rtName === 'int' => '0',
                $rtName === 'float' => '0.0',
                $rtName === 'string' => "''",
                $rtName === 'array' => '[]',
                $rtName === 'mixed' => 'null',
                default => "throw new \\RuntimeException('stub: {$m->getName()} has no default, override it')",
            };
            $body = "if (\$this->__has_override('{$m->getName()}')) { "
                . "return \$this->__call_override('{$m->getName()}', func_get_args()); } "
                . ($default === '$this' ? "return \$this;" :
                    (str_starts_with($default, 'throw') ? "$default;" : "return $default;"));
        }

        $methods[] = 'public function ' . $m->getName() . '(' . implode(', ', $params) . ')'
            . $returnDecl . " { $body }";
    }

    $name = 'Stub_' . $counter++ . '_' . str_replace('\\', '_', $class);
    $extends = $ref->isInterface() ? "implements \\$class" : "extends \\$class";
    $code = "class $name $extends {\n"
        . "public array \$__overrides = [];\n"
        . "public array \$__calls = [];\n"
        . "public function __has_override(string \$m): bool { return array_key_exists(\$m, \$this->__overrides); }\n"
        . "public function __call_override(string \$m, array \$args) { \$this->__calls[] = [\$m, \$args];\n"
        . "  if (!\$this->__has_override(\$m)) { return null; }\n"
        . "  \$v = \$this->__overrides[\$m]; return \$v instanceof \\Closure ? \$v(...\$args) : \$v; }\n"
        . implode("\n", $methods) . "\n}";
    eval($code);

    $obj = new $name();
    $obj->__overrides = $overrides;
    return $obj;
}

const ASS_ID = 1;
const CONTEXT_ID = 123;
const USER_ID = 6;
const KEY = 'smoke-test-provider-key';

function makeApp(ComponentApiFactory $apis): AppProvider
{
    $token = stub(Token::class, ['getToken' => 'tok', 'getUserId' => USER_ID, 'getAssId' => ASS_ID]);
    $auth = stub(AuthFullService::class, ['newToken' => $token, 'getToken' => $token]);
    $repos = stub(Repositories::class, [
        'properties' => stub(\Edutiek\AssessmentService\Assessment\Data\PropertiesRepo::class, ['exists' => true]),
    ]);
    $permissions = stub(Permissions::class, ['canDoRestCall' => true]);
    $config = stub(ConfigReadService::class, [
        'getConfig' => stub(Config::class, ['getSimulateOffline' => false]),
    ]);
    $users = stub(UserReadService::class, ['getUser' => stub(UserData::class)]);
    $delivery = stub(Delivery::class);
    $context = stub(RestContext::class, ['getParams' => []]);

    $helper = new RestHelper(
        ASS_ID, CONTEXT_ID, USER_ID,
        $auth, $permissions, $repos, $config, $users, $delivery, $context
    );

    return new AppProvider(
        ASS_ID, CONTEXT_ID, USER_ID,
        $permissions, $helper, $apis, AppFactory::create(), $context, $delivery
    );
}

function request(string $method, string $path, string $key = ''): ServerRequestInterface
{
    $request = (new ServerRequestFactory())->createServerRequest($method, $path)
        ->withQueryParams(['ass_id' => ASS_ID, 'context_id' => CONTEXT_ID, 'user_id' => USER_ID]);
    if ($key !== '') {
        $request = $request->withHeader('X-Provider-Key', $key);
    }
    return $request;
}

function check(string $label, bool $ok): void
{
    fwrite(STDERR, ($ok ? 'PASS' : 'FAIL') . " $label\n");
    if (!$ok) {
        exit(1);
    }
}

putenv('XLAS_PROVIDER_KEY=' . KEY);
$_SERVER['REMOTE_ADDR'] = '127.0.0.1';

$mode = $argv[1] ?? 'auth';

if ($mode === 'auth') {
    $apis = stub(ComponentApiFactory::class, ['components' => [], 'api' => null, 'allComponents' => []]);
    $app = makeApp($apis);

    // wrong key -> 401
    try {
        $app->getData(request('GET', '/provider/data', 'wrong-key'), new Response(), []);
        check('wrong provider key is rejected', false);
    } catch (RestException $e) {
        check('wrong provider key is rejected with 401', $e->getCode() === RestException::UNAUTHORIZED);
    }

    // missing key -> 401
    try {
        $app->getData(request('GET', '/provider/data'), new Response(), []);
        check('missing provider key is rejected', false);
    } catch (RestException $e) {
        check('missing provider key is rejected with 401', $e->getCode() === RestException::UNAUTHORIZED);
    }

    // unset env var -> everything rejected
    putenv('XLAS_PROVIDER_KEY');
    try {
        $app->getData(request('GET', '/provider/data', KEY), new Response(), []);
        check('unset env var rejects all calls', false);
    } catch (RestException $e) {
        check('unset env var rejects all calls with 401', $e->getCode() === RestException::UNAUTHORIZED);
    }
    putenv('XLAS_PROVIDER_KEY=' . KEY);

    // correct key -> 200 with json body
    $response = $app->getData(request('GET', '/provider/data', KEY), new Response(), []);
    check('correct key returns 200', $response->getStatusCode() === 200);
    check('response is json', $response->getHeaderLine('Content-Type') === 'application/json');
    // note: no check for the xlasDataToken header - BaseApp::getData discards
    // the response returned by extendDataToken (upstream behavior), the
    // provider uses the static key anyway

    // correct key via query param -> 200
    $request = request('GET', '/provider/data')
        ->withQueryParams(['ass_id' => ASS_ID, 'context_id' => CONTEXT_ID, 'user_id' => USER_ID, 'provider_key' => KEY]);
    $response = $app->getData($request, new Response(), []);
    check('key via query param returns 200', $response->getStatusCode() === 200);

    fwrite(STDERR, "auth smoke test ok\n");
    exit(0);
}

if ($mode === 'changes') {
    // RestHelper::getJsonData reads php://input, which is empty in cli mode -
    // replace the php stream wrapper so php://input serves our stdin
    $wrapper = new class () {
        public static string $body = '';
        public $context;
        private int $pos = 0;
        public function stream_open(string $path): bool
        {
            return $path === 'php://input';
        }
        public function stream_read(int $count): string
        {
            $chunk = substr(self::$body, $this->pos, $count);
            $this->pos += strlen($chunk);
            return $chunk;
        }
        public function stream_eof(): bool
        {
            return $this->pos >= strlen(self::$body);
        }
        public function stream_stat(): array
        {
            return [];
        }
    };
    $wrapperClass = get_class($wrapper);
    $wrapperClass::$body = stream_get_contents(STDIN);

    // recording bridge: accepts every change and records type, key, action, payload
    $applied = [];
    $bridge = stub(AppCorrectorBridge::class, [
        'applyChanges' => function (string $type, array $changes) use (&$applied) {
            return array_map(function ($change) use ($type, &$applied) {
                $applied[$type][] = [
                    'key' => $change->getKey(),
                    'action' => $change->getAction()?->value,
                    'payload' => $change->getPayload(),
                ];
                return $change->toResponse(true);
            }, $changes);
        },
    ]);
    $api = stub(\Edutiek\AssessmentService\Assessment\Api\ComponentApi::class, [
        'correctorBridge' => $bridge,
    ]);
    $apis = stub(ComponentApiFactory::class, [
        'components' => ['Task'],
        'allComponents' => ['Task'],
        'api' => fn(string $component) => strtolower($component) === 'task' ? $api : null,
    ]);
    $app = makeApp($apis);

    // body comes from stdin (php://input in cli mode); Content-Type must be json
    $request = request('PUT', '/provider/changes', KEY)
        ->withHeader('Content-Type', 'application/json');
    $response = new Response();

    // swap the wrapper only around the call - slim needs php://temp elsewhere
    stream_wrapper_unregister('php');
    stream_wrapper_register('php', $wrapperClass);
    try {
        $response = $app->putChanges($request, $response, []);
    } finally {
        stream_wrapper_restore('php');
    }

    echo json_encode([
        'status' => $response->getStatusCode(),
        'applied' => $applied,
        'response' => json_decode((string) $response->getBody(), true),
    ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) . "\n";
    exit(0);
}

fwrite(STDERR, "unknown mode: $mode\n");
exit(2);
