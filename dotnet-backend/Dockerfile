FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY src/FinCore.Domain/FinCore.Domain.csproj src/FinCore.Domain/
COPY src/FinCore.Application/FinCore.Application.csproj src/FinCore.Application/
COPY src/FinCore.Infrastructure/FinCore.Infrastructure.csproj src/FinCore.Infrastructure/
COPY src/FinCore.Api/FinCore.Api.csproj src/FinCore.Api/
RUN dotnet restore src/FinCore.Api/FinCore.Api.csproj
COPY src/ src/
RUN dotnet publish src/FinCore.Api/FinCore.Api.csproj -c Release -o /out --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app
ENV ASPNETCORE_HTTP_PORTS=8080
EXPOSE 8080
COPY --from=build /out .
ENTRYPOINT ["dotnet", "FinCore.Api.dll"]
