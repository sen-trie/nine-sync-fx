// Scene geometry pass, with an owner distinct from the model being drawn.
float4x4 WVP : WORLDVIEWPROJECTION;
float OwnerScale : CONTROLOBJECT < string name="(OffscreenOwner)"; string item="Si"; >;
float4x4 OwnerWorld : CONTROLOBJECT < string name="(OffscreenOwner)"; >;
float3 TargetOffset : CONTROLOBJECT < string name="TargetCamera.x"; >;
float4 D : DIFFUSE < string Object="Geometry"; >;
struct Output { float4 p:POSITION; float4 color:COLOR0; };
Output VS(float4 p:POSITION) {
 Output o;
 p.xyz -= TargetOffset;
 o.p=mul(p,WVP);
 o.color=float4(OwnerScale*.015,.3+OwnerWorld[0][0]*.01,.2+OwnerWorld[3][0]*.02,D.a);
 return o;
}
float4 PS(Output i):COLOR { return i.color; }
technique Main < string MMDPass="object"; > {
 pass Draw { VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS(); }
}
technique NoEdge < string MMDPass="edge"; > {}
technique NoShadow < string MMDPass="shadow"; > {}
