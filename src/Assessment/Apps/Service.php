<?php

declare(strict_types=1);

namespace Edutiek\AssessmentService\Assessment\Apps;

use Edutiek\AssessmentService\Assessment\Api\Internal;
use Edutiek\AssessmentService\Assessment\TaskInterfaces\GradingStatus;
use Throwable;
use Edutiek\AssessmentService\System\Config\Frontend;
use Edutiek\AssessmentService\System\Config\ReadService as ConfigService;

class Service implements OpenService, RestService
{
    private Frontend $frontend;
    private int $ass_id;
    private int $context_id;
    private int $user_id;

    public function __construct(
        private readonly ConfigService $config,
        private readonly RestContext $context,
        private readonly Internal $internal
    ) {
    }

    /**
     * Init the service properties for a use by the client api
     * Here the assessment and user ids are already provided by the API
     */
    public function initForClientApi(int $ass_id, int $user_id): self
    {
        $this->ass_id = $ass_id;
        $this->user_id = $user_id;
        return $this;
    }

    /**
     * Open the writer frontend
     */
    public function openWriter(int $context_id, string $return_url): never
    {
        $this->frontend = Frontend::WRITER;
        $this->context_id = $context_id;
        $helper = $this->internal->openHelper($this->ass_id, $this->context_id, $this->user_id);
        $helper->setCommonFrontendParams($return_url);
        $helper->openFrontend($this->config->getFrontendUrl($this->frontend));
    }

    /**
     * Open the corrector frontend
     */
    public function openCorrector(int $context_id, string $return_url, ?int $task_id, ?int $writer_id, ?bool $as_admin = false): void
    {
        $this->frontend = Frontend::CORRECTOR;
        $this->context_id = $context_id;
        $permissions = $this->internal->permissions($this->ass_id, $this->context_id, $this->user_id);
        $helper = $this->internal->openHelper($this->ass_id, $this->context_id, $this->user_id);
        $helper->setCommonFrontendParams($return_url);
        $helper->setFrontendParam('TaskId', (string) $task_id);
        $helper->setFrontendParam('WriterId', (string) $writer_id);
        $helper->setFrontendParam('AsAdmin', ($as_admin && $permissions->canMaintainCorrectors()) ? 'true' : 'false');
        $helper->openFrontend($this->config->getFrontendUrl($this->frontend));
    }

    /**
     * Init the service from a REST call
     * Here the properties have to be extracted from the call
     */
    private function initForRestCall(): void
    {
        $params = $this->context->getParams();
        foreach (['ass_id', 'context_id', 'user_id'] as $key) {
            if (!isset($params[$key])) {
                throw new RestException("Query parameter $key is missing", RestException::NOT_FOUND);
            }
        }
        $this->ass_id = (int) $params['ass_id'];
        $this->context_id = (int) $params['context_id'];
        $this->user_id = (int) $params['user_id'];

        $parts = explode('/', $this->context->getRoute());
        $this->frontend = Frontend::fromRoutePart($parts[1]);
        if ($this->frontend === null) {
            throw new RestException("Frontend not found by the REST route", RestException::NOT_FOUND);
        }

        // init the hosting system for the REST call (e.g. set the current user)
        $this->context->initCall($this->ass_id, $this->context_id, $this->user_id);
    }

    /**
     * Handle a REST call
     */
    public function handle(): never
    {
        try {
            $this->initForRestCall();
            $this->getApp()->handle();
        } catch (RestException $e) {
            $this->context->sendResponse($e->getCode(), $e->getMessage());
        } catch (Throwable $e) {
            $this->context->sendResponse(RestException::INTERNAL_SERVER_ERROR,
                $e->getMessage() . "\n". $e->getTraceAsString());
        }
        exit;
    }

    /**
     * Get the app handler
     */
    private function getApp(): BaseApp
    {
        switch ($this->frontend) {
            case Frontend::WRITER:
                return $this->internal->appWriter($this->ass_id, $this->context_id, $this->user_id);
            case Frontend::CORRECTOR:
                return $this->internal->appCorrector($this->ass_id, $this->context_id, $this->user_id);
            case Frontend::PROVIDER:
                return $this->internal->appProvider($this->ass_id, $this->context_id, $this->user_id);
        };
        $this->context->sendResponse(RestException::NOT_IMPLEMENTED, "Handler for frontend '{$this->frontend->value}' not implemented.");

    }
}
