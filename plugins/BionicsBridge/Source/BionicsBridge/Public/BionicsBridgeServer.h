// Copyright Jacob Ribbe. Licensed under MIT.

#pragma once

#include "CoreMinimal.h"
#include "HttpRouteHandle.h"
#include "HttpResultCallback.h"
#include "HttpServerRequest.h"

/**
 * HTTP server for BionicsBridge.
 *
 * Binds localhost:port and exposes a JSON-RPC 2.0 endpoint at /bridge.
 * Supported methods: initialize, tools/list, tools/call, shutdown.
 *
 * All tool execution is marshaled to the GAME THREAD via AsyncTask.
 */
class FBionicsBridgeServer
{
public:
	FBionicsBridgeServer();
	~FBionicsBridgeServer();

	/** Start the server on the specified port. Returns true if bound. */
	bool Start(int32 Port);

	/** Stop the server and release the port. */
	void Stop();

	/** Check if server is currently listening. */
	bool IsRunning() const { return bRunning; }

	/** Get the port the server is bound to (0 if not running). */
	int32 GetPort() const { return BoundPort; }

	/**
	 * Set the required bearer token for POST /bridge requests.
	 * Must be called before Start() for the token to be enforced for the whole session.
	 * Empty token disables auth (dev-only fallback — logs a warning at start).
	 */
	void SetAuthToken(const FString& InToken) { AuthToken = InToken; }

	/** Get the current auth token (for discovery file export). */
	const FString& GetAuthToken() const { return AuthToken; }

private:
	bool bRunning = false;
	int32 BoundPort = 0;
	FString AuthToken;  // Bearer token required on POST /bridge (empty = disabled, with warning)
	FHttpRouteHandle BridgeRoute;
	FHttpRouteHandle OptionsRoute;
	FHttpRouteHandle HealthRoute;

	/** Check Authorization: Bearer <token> header against AuthToken. Returns true if OK or auth disabled. */
	bool IsAuthorized(const FHttpServerRequest& Request) const;

	/** Build a JSON-RPC 401 unauthorized response. Echoes the request's loopback Origin. */
	TUniquePtr<struct FHttpServerResponse> MakeUnauthorizedResponse(const FHttpServerRequest& Request) const;

	/** Handle a POST /bridge JSON-RPC request. */
	bool HandleBridgeRequest(const FHttpServerRequest& Request,
	                          const FHttpResultCallback& OnComplete);

	/** Handle GET /bridge health check. */
	bool HandleHealthRequest(const FHttpServerRequest& Request,
	                          const FHttpResultCallback& OnComplete);

	/** Handle OPTIONS for CORS. */
	bool HandleOptionsRequest(const FHttpServerRequest& Request,
	                           const FHttpResultCallback& OnComplete);

	/** Process a parsed JSON-RPC envelope (runs on game thread). */
	void DispatchRpc(const TSharedPtr<class FJsonObject>& Request,
	                 TSharedPtr<class FJsonObject>& OutResponse);

	/** Build a JSON-RPC error response. */
	static TSharedPtr<class FJsonObject> MakeErrorResponse(
		const TSharedPtr<class FJsonValue>& Id, int32 Code, const FString& Message);
};
