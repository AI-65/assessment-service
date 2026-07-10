<?php

declare(strict_types=1);

namespace Edutiek\AssessmentService\System\Config;

enum Frontend: string
{
    case WRITER = 'writer';
    case CORRECTOR = 'corrector';

    // REST-only pseudo frontend for external correction providers
    // it has no web app that can be opened, see AppProvider
    case PROVIDER = 'provider';

    /**
     * Get the frontend from the first part of a REST route
     */
    public static function fromRoutePart($route): ?self
    {
        return self::tryFrom($route);
    }

    /**
     * Name and directory of the node module
     * This is used to build the URL for opening the frontend
     */
    public function module(): string
    {
        return match ($this) {
            self::WRITER => 'assessment-writer',
            self::CORRECTOR => 'assessment-corrector',
            self::PROVIDER => 'assessment-provider',
        };
    }
}
