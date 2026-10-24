// Fullscreen-triangle vertex shader whose outputs match ps_deferredRendering's
// input signature (SV_Position, TEXCOORD0, INSTANCE_ID), for tools/lab/psotest.

struct VertexOut
{
    float4 Position : SV_Position;
    float4 TexCoord : TEXCOORD0;
    nointerpolation uint InstanceId : INSTANCE_ID;
};

VertexOut main(uint vertex : SV_VertexID, uint instance : SV_InstanceID)
{
    float2 uv = float2((vertex << 1) & 2, vertex & 2);
    VertexOut o;
    o.Position = float4(uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    o.TexCoord = float4(uv, 0.0, 0.0);
    o.InstanceId = instance;
    return o;
}
