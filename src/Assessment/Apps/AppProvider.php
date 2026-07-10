<?php

declare(strict_types=1);

namespace Edutiek\AssessmentService\Assessment\Apps;

use Edutiek\AssessmentService\Assessment\Data\TokenPurpose;
use Psr\Http\Message\ResponseInterface as Response;
use Psr\Http\Message\ServerRequestInterface as Request;

/**
 * REST endpoints for external correction providers (proof of concept).
 *
 * An external provider (e.g. an AI correction service) acts as a regular
 * corrector: the user given by the 'user_id' query parameter must exist in
 * the hosting system, needs read access to the assessment and has to be
 * registered as a corrector with assignments for the items it should
 * correct. All scope and workflow checks of the corrector bridges apply
 * unchanged, so a provider can only write comments, points and summaries
 * for its own corrector record.
 *
 * The routes mirror the corrector app:
 *   GET /provider/data                        - settings, items, tasks, criteria context
 *   GET /provider/item/{task_id}/{writer_id}  - essay text, criteria, own corrections
 *   GET /provider/file/{component}/{entity}/{id}/{dummy}
 *   PUT /provider/changes                     - comments, points, summary
 *
 * Authentication (PoC only, not for production): the hosting system must
 * set the environment variable XLAS_PROVIDER_KEY and the request must send
 * the same value in the 'X-Provider-Key' header or the 'provider_key'
 * query parameter. If the variable is not set, all provider calls are
 * rejected. The session token handling of the interactive apps is not
 * used; a data token is created on the fly so that responses which extend
 * it keep working.
 */
class AppProvider extends AppCorrector
{
    public const KEY_ENV_VAR = 'XLAS_PROVIDER_KEY';

    public function handle(): never
    {
        $this->app->get('/provider/data', [$this,'getData']);
        $this->app->get('/provider/item/{task_id}/{writer_id}', [$this,'getItem']);
        $this->app->get('/provider/file/{component}/{entity}/{id}/{dummy}', [$this,'getFile']);
        $this->app->put('/provider/changes', [$this, 'putChanges']);
        $this->app->run();
        exit;
    }

    /**
     * Prepare handling the REST call
     * Replaces the token/signature check of the interactive apps
     * with a static provider key check
     */
    protected function prepare(Request $request, Response $response, array $args, TokenPurpose $purpose): void
    {
        $configured = getenv(self::KEY_ENV_VAR);

        $sent = $request->getHeaderLine('X-Provider-Key');
        if ($sent === '') {
            $sent = (string) ($request->getQueryParams()['provider_key'] ?? '');
        }

        if (!is_string($configured) || $configured === '' || !hash_equals($configured, $sent)) {
            throw new RestException('provider key missing or wrong', RestException::UNAUTHORIZED);
        }

        // a provider has no interactive session, but some responses extend
        // the data token of the current user, so one has to exist
        $this->rest_helper->setNewDataToken($response);

        $this->rest_helper->checkAccess();
    }
}
